import hashlib
import json
import logging
import os
import threading
import time
from decimal import Decimal
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Idempotency Gateway")

# TODO: redis, this dies on restart
cache = {}
in_flight = {}
lock = threading.Lock()

TTL = int(os.getenv("IDEMPOTENCY_TTL", 86400))
CURRENCIES = set(os.getenv("SUPPORTED_CURRENCIES", "RWF,GHS").split(","))


class PaymentRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v):
        v = v.upper()
        if v not in CURRENCIES:
            raise ValueError(f"Currency must be one of: {', '.join(sorted(CURRENCIES))}")
        return v


def hash_payment(payment):
    # str() so 10.5 and 10.50 dont hash differently
    data = json.dumps({"amount": str(payment.amount), "currency": payment.currency}, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


def check_rate_limit(key: str) -> None:
    # TODO: add per-key rate limiting
    pass


def purge_expired():
    now = time.time()
    expired = [k for k in cache if now - cache[k]["created_at"] >= TTL]
    if expired:
        logger.info("Purging %d expired keys", len(expired))
    for k in expired:
        del cache[k]
        in_flight.pop(k, None)


def _background_cleanup():
    while True:
        time.sleep(300)
        with lock:
            try:
                purge_expired()
            except Exception:
                logger.exception("cleanup failed")


cleanup_thread = threading.Thread(target=_background_cleanup, daemon=True)
cleanup_thread.start()


@app.get("/health")
def health():
    return {"status": "ok", "cache_size": len(cache)}


@app.post("/process-payment")
def process_payment(
    payment: PaymentRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required.")

    check_rate_limit(idempotency_key)

    req_hash = hash_payment(payment)
    pending_event = None

    with lock:
        existing = cache.get(idempotency_key)

        if existing is not None:
            if existing["status"] == "processing":
                # tried sleep(0.1) polling first but Event is cleaner
                # FIXME: hangs full 30s if original caller dies, no heartbeat
                pending_event = in_flight[idempotency_key]
            elif existing["body_hash"] != req_hash:
                logger.warning("key %r reused with different body", idempotency_key)
                raise HTTPException(status_code=422, detail="This key was already used with a different request body.")
            else:
                logger.debug(f"cache hit: {idempotency_key!r}")
                return JSONResponse(
                    content=existing["response"],
                    status_code=existing["status_code"],
                    headers={"X-Cache-Hit": "true"},
                )
        else:
            event = threading.Event()
            in_flight[idempotency_key] = event
            cache[idempotency_key] = {
                "body_hash": req_hash,
                "status": "processing",
                "response": None,
                "status_code": None,
                "created_at": time.time(),
            }

    if pending_event is not None:
        logger.info("waiting on in-flight: %r", idempotency_key)
        pending_event.wait(timeout=30)
        with lock:
            existing = cache.get(idempotency_key)
        if existing and existing["status"] == "done":
            return JSONResponse(
                content=existing["response"],
                status_code=existing["status_code"],
                headers={"X-Cache-Hit": "true"},
            )
        # timed out - orphaned entry stays in cache until TTL, not ideal
        raise HTTPException(status_code=503, detail="Timed out waiting for in-flight request.")

    try:
        logger.info(f"processing {payment.amount} {payment.currency}")
        time.sleep(2)

        response = {"status": "success", "message": f"Charged {payment.amount} {payment.currency}"}
        status_code = 201

        with lock:
            cache[idempotency_key]["status"] = "done"
            cache[idempotency_key]["response"] = response
            cache[idempotency_key]["status_code"] = status_code
            done_event = in_flight.pop(idempotency_key, None)

        if done_event:
            done_event.set()

        return JSONResponse(content=response, status_code=status_code)

    except Exception:
        with lock:
            cache.pop(idempotency_key, None)
            failed_event = in_flight.pop(idempotency_key, None)
        if failed_event:
            failed_event.set()
        raise
