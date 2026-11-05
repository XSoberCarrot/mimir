#!/usr/bin/env python3
"""ZMQ PULL -> ROS /tts: text pushed by the server's speak_assistant.py is published for tts_stream_node.py.

Replaces Chang's speech_bridge_node (port 5560) with port 5562, which nothing else uses, so it can start
after everything.launch.py (its speech_processor zmq_bridge holds 5560).

Run with ROS 2 (foxy) sourced, see speech_nodes.sh.
    python3 speech_bridge.py [--port 5562]
"""
import argparse
import os

import rclpy
import zmq
from std_msgs.msg import String


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("SPEECH_PORT", 5562)))
    args = ap.parse_args()

    rclpy.init()
    node = rclpy.create_node("speech_bridge_zmq")
    pub = node.create_publisher(String, "/tts", 10)
    sock = zmq.Context.instance().socket(zmq.PULL)
    sock.bind(f"tcp://*:{args.port}")
    log = node.get_logger()
    log.info(f"Speech bridge: ZMQ PULL :{args.port} -> /tts")
    try:
        while rclpy.ok():
            text = sock.recv_string().strip()
            if text:
                pub.publish(String(data=text))
                log.info(f"-> /tts: {text!r}")
    except KeyboardInterrupt:
        pass
    finally:
        sock.close(linger=0)
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
