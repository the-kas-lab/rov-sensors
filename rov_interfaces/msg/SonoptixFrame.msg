# One Sonoptix ECHO multibeam frame (one decoded image of its raw RTSP stream).
#
# intensity is row-major, num_samples rows x num_beams columns: row i is the
# echo strength at range_m[i] (nearest first), column j is beam j at
# beam_angle_deg[j] (port to starboard). Element (i, j) is intensity[i * num_beams + j].

std_msgs/Header header        # stamp: host time the frame was received; frame_id: transducer frame

uint32 frame_number           # counts up from 0 when the node connects to the stream
float32 sonar_range_m         # range setting (the end of range_m)
uint8 operation_mode          # 0 = AUTO, 1 = HF, 2 = LF, 3 = LFLR (low frequency, long range)
uint32 frequency_hz           # 700000 or 400000, nominal; inferred from operation_mode and range
float32 sector_deg            # horizontal field of view covered by the beams (120 at 700 kHz, 90 at 400 kHz)

uint32 num_samples            # rows: about 67 per metre of range, at most 1024
uint32 num_beams              # columns: 256
float32[] range_m             # distance of each row from the transducer
float32[] beam_angle_deg      # bearing of each column; 0 = straight ahead, positive = starboard
uint8[] intensity             # echo strength 0-255, num_samples x num_beams
