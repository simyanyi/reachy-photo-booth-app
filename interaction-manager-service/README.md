# Interaction Manager Service

## Overview

The Interaction Manager coordinates the robot's state and conversation flow, ensuring seamless, event-driven interaction between the user, robot, and AI agent.

## Running the Service

### Prerequisites

Start the required infrastructure services:

| Service | Description |
|---------|-------------|
| `redpanda` | Message broker for Kafka communication |
| `robot-controller` | Robot hardware control service |
| `agent` | LLM-powered agent service |

These can be started via Docker Compose from the repository root.

### Starting the Service

```bash
uv run interaction-manager-service/src/main.py
```

## Configuration

The default configuration lets you run the interaction manager out of the box. To customize settings, edit the relevant fields in [compose.yaml](compose.yaml).

The most commonly adjusted options are:

```yaml
configs:
  config_interaction_manager:
    content: |
      robot_utterances_path: /app/data/robot_utterances.yaml
      robot_config:
        photo_booth_bot:
          voice_config:
            pitch_shift: <e.g. -0.1>
            word_speed_range: <e.g. 0.6>
            skip_chance: <e.g. 0.1>
          animation_config:
            find_angle_step: <e.g. 45.0>
            find_range: <e.g. 180.0>
          light_config:
            focus_duration: <e.g. 50.0>
          center_user_timeout: <e.g. 10.0>
          enable_listening_while_speaking: <true|false>
      tool_names:
        start:
          - <tools_that_start_interaction>
        human:
          - <tools_that_require_human_input>
        image_generation:
          - <tools_that_generate_images>
        taking_picture:
          - <tools_that_take_pictures>
        end:
          - <tools_that_end_interaction>
      room_mapping:
        screen: <e.g. {x: -1.5, y: -1.2}>
      global_clip_volume: <e.g. 1.0>
      time_between_comments:
        min: <e.g. 1.5>
        max: <e.g. 3.0>
```

To learn more about all available configurations, refer to the [Interaction Manager configuration](src/configuration.py).

## Robot Utterances

The robot's spoken phrases are defined in [data/robot_utterances.yaml](data/robot_utterances.yaml). You can customize what the robot says during different events:

- `wake_up` – Phrases when the robot wakes up
- `look_at_human` – Phrases when taking a picture (`started` and `completed`)
- `generate_image` – Phrases when image generation completes
- `qr_code_preparation` – Phrases when preparing to show a QR code
- `demo_information` – Fun facts about the demo (`performance` and `facts`)

## Room Mapping

The `room_mapping` configuration tells the robot where objects are located so it can look at them. The robot is at the origin `(0, 0)`.

**Coordinate conventions:**
- **X**: positive direction = the front (viewed from the robot) pointing towards the user
- **Y**: positive direction = left side (viewed from the robot)
- Only direction matters (not distance), so `(1, 1)` and `(2, 2)` point the same way

Configure positions in the `room_mapping` section of [compose.yaml](compose.yaml):

```yaml
room_mapping:
  screen: {x: -1.5, y: -1.2}
```

![Reachy Coordinate System Top View](../docs/images/reachy-coordinate-system-top-view.png)

## Speaking and photo capture behavior

Speech uses `talkingForward`, a five-second looping animation with gentle ±6°
pitch nods, antenna movement, and no animated body yaw, head yaw, or roll.
The `look_direction` argument no longer selects shoulder-turning clips. Active
person-tracking motion is paused during speech and resumed afterward only when
still in the tracking state. Existing compositor joint limits still apply.

A photo request searches for a person and requires a new person detection
before entering photo preparation. The tracker stays enabled during speech and
the countdown, even while tracking motion is paused. A second new detection is
required after the countdown before the capture tool is acknowledged. Missing people cancel the request instead of taking a photo anyway.
`center_user_timeout` limits each fresh-detection wait; the complete agent preparation
has a 45-second limit (below the agent's 60-second acknowledgement timeout).
The remote-control photo sequence also uses the detection checks and a bounded
30-second wait. Its existing action is a photo animation, not an HTTP capture.

Capture uses `workmesh.photo_gate.PhotoGate` to require a valid person detection
received within 1.5 seconds. It does not require the full-body box to be centered:
the motion tracker targets the nose/neck instead, and those centers differ.
Duplicate frames do not refresh presence. The camera also checks the detected frame index against its current
stream before serving `/capture`. The camera preview remains available while
searching. Locked body/head limits may require the person to step into view.

After updating, rebuild the affected service images from the repository root:

```bash
docker compose up -d --build --no-deps agent camera interaction-manager animation-database
```

Hardware-free regression checks:

```bash
python3 -m unittest discover -s tests/unit -v
```

These tests cover the shared detection gate and service control flow with mocked
hardware and messaging. Live camera, robot motion, and Kafka integration still
need checking on the running booth.

After capture, the original agent decision flow, image-generation prompt building,
processing comments, completion speech, QR handling, and farewell remain in place.
The original capture-to-generation workflow instructions are restored. There is
no separate forced-processing node or replacement image-prompt writer. Creative
preference questions still happen before capture.
