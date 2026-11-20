"""WebRTC spike, VM side: offerer + echo peer.

Writes offer SDP to offer.txt, then waits for answer.txt to appear,
connects, echoes any DataChannel message back, logs ICE states.
Exits 0 after the first successful echo round-trip.
"""
import asyncio, json, sys, os
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 1800


async def main():
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[
        RTCIceServer(urls="stun:stun.l.google.com:19302"),
    ]))

    @pc.on("iceconnectionstatechange")
    async def _ice():
        print(f"[vm] iceConnectionState={pc.iceConnectionState}", flush=True)
        if pc.iceConnectionState == "failed":
            print("[vm] ICE FAILED", flush=True)
            sys.exit(2)

    @pc.on("connectionstatechange")
    async def _conn():
        print(f"[vm] connectionState={pc.connectionState}", flush=True)

    channel = pc.createDataChannel("pack-spike")
    done = asyncio.get_event_loop().create_future()

    @channel.on("open")
    def _open():
        print("[vm] datachannel open", flush=True)

    @channel.on("message")
    def _msg(m):
        print(f"[vm] got message: {m!r}", flush=True)
        channel.send("echo:" + m)
        if not done.done():
            done.set_result(m)

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    # wait for ICE gathering to complete
    for _ in range(100):
        if pc.iceGatheringState == "complete":
            break
        await asyncio.sleep(0.1)
    print(f"[vm] iceGatheringState={pc.iceGatheringState}", flush=True)
    with open(os.path.join(HERE, "offer.txt"), "w") as f:
        json.dump({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}, f)
    print("[vm] offer written to offer.txt, waiting for answer.txt", flush=True)

    ans_path = os.path.join(HERE, "answer.txt")
    for _ in range(TIMEOUT * 2):
        if os.path.exists(ans_path):
            break
        await asyncio.sleep(0.5)
    else:
        print("[vm] no answer within timeout", flush=True)
        sys.exit(3)
    with open(ans_path) as f:
        ans = json.load(f)
    await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
    print("[vm] answer applied", flush=True)

    try:
        got = await asyncio.wait_for(done, timeout=60)
        print(f"[vm] ECHO ROUND-TRIP OK: {got!r}", flush=True)
        await asyncio.sleep(1)
        await pc.close()
        sys.exit(0)
    except asyncio.TimeoutError:
        print("[vm] channel never delivered a message", flush=True)
        sys.exit(4)


asyncio.run(main())
