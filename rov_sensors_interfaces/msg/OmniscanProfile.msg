# One Cerulean Omniscan 450 FS ping (the sonar's os_mono_profile message).
#
# power_db[i] is the echo strength coming back from range_m[i] metres in front
# of the transducer. Both arrays have one entry per sample.

std_msgs/Header header        # stamp: host time the ping was received; frame_id: transducer frame

uint32 ping_number            # counts up from 0 at sonar power-up
uint32 timestamp_ms           # sonar clock, ms since power-up, at the time of the ping
uint32 ping_hz                # acoustic frequency of the pulse
uint8 gain_index              # receiver gain 0-7
float32 speed_of_sound        # m/s used to turn echo time into distance
float32 start_m               # start of the scan window
float32 length_m              # length of the scan window
float32 pulse_duration_sec
float32 analog_gain
float32 min_pwr_db            # scale the sonar used to pack its u16 samples
float32 max_pwr_db
float32 transducer_heading_deg
float32 vehicle_heading_deg

float32[] range_m             # distance of each sample from the transducer
float32[] power_db            # echo strength of each sample, dB
