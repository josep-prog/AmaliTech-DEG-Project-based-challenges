This project is a Pulse-Check API (Watchdog Sentinel), which I built to monitor remote devices and detect when they stop communicating. The idea behind it is: each device sends a signal called a heartbeat at regular intervals. I treat each heartbeat as proof that the device is still active. If a device stops sending these signals within a defined time, the system automatically raises an alert to indicate a possible failure.

### **User Story 1: Registering a Monitor**

I start by allowing a device to be registered for monitoring using *POST /monitors*. The request includes a device ID, a timeout, and an alert email. I validate this data to make sure nothing is missing and the timeout stays within safe limits. Once everything is correct, I store the device as a monitor and start a background timer. This timer defines how long the system should wait for a heartbeat before considering the device inactive. As soon as registration is complete, the device becomes active in the monitoring system.

### 

### **User Story 2: The Heartbeat (Reset)**

Each device sends heartbeats using *POST /monitors/{id}/heartbeat*. When I receive a heartbeat, I first confirm that the device exists. If it does, I reset its timer back to the original timeout value, which starts a fresh monitoring window. I also record the exact time of each heartbeat so I can track device activity over time. After this process, I return a confirmation message showing that the reset was successful.

### **User Story 3: The Alert (Failure State)**

If a device does not send a heartbeat before the timer reaches zero, I mark it as inactive. At that point, I trigger an internal alert function. I update the device status to “down” and generate an alert message in this format:

{"ALERT": "Device device-123 is down\!", "time": ...}

This alert is meant for system operators so they can react quickly. In a real deployment, it can be forwarded to email services or monitoring dashboards.

### **Bonus User Story: Pause (Snooze Feature)**

I added a maintenance mode using *POST /monitors/{id}/pause*. When this endpoint is called, I stop monitoring that device. The timer is cancelled and the device status becomes “paused”. While in this state, the system ignores time passing and does not generate any alerts. If a heartbeat comes in after pausing, I automatically bring the device back to active monitoring. I restart the timer from the full timeout value and continue normal tracking.

### **Developer’s Choice Feature: Heartbeat History Tracking**

I also track every heartbeat received from each device. Each timestamp is stored in a history list linked to that device. To keep the system efficient, I limit this history to the most recent 50 entries. This helps me understand how each device behaves over time and makes it easier to detect unusual communication patterns during monitoring analysis.

