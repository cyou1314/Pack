"""WebRTC spike, Mac side: answerer. Run with the spike venv python
(same aiortc version as the VM side). Reads offer.txt (copied from the
VM), writes answer.txt, on channel open sends 'ping-from-mac' and
expects 'echo:ping-from-mac' back. Exits 0 on success.
"""
import asyncio, json, sys, os
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer

HERE = os.path.dirname(os.path.abspath(__file__))


async def main():
    with open(os.path.join(HERE, "offer.txt")) as f:
        offer = json.load(f)
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[
        RTCIceServer(urls="stun:stun.l.google.com:19302"),
    ]))
    done = asyncio.get_event_loop().create_future()

    @pc.on("iceconnectionstatechange")
    async def _ice():
        print(f"[mac] iceConnectionState={pc.iceConnectionState}", flush=True)
        if pc.iceConnectionState == "failed" and not done.done():
            done.set_exception(RuntimeError("ICE failed"))

    @pc.on("datachannel")
    def _dc(channel):
        print(f"[mac] datachannel: {channel.label}", flush=True)

        @channel.on("open")
        def _open():
            print("[mac] channel open, sending ping", flush=True)
            channel.send("ping-from-mac")

        @channel.on("message")
        def _msg(m):
            print(f"[mac] got message: {m!r}", flush=True)
            if m == "echo:ping-from-mac" and not done.done():
                done.set_result(m)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=offer["sdp"], type=offer["type"]))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    for _ in range(100):
        if pc.iceGatheringState == "complete":
            break
        await asyncio.sleep(0.1)
    print(f"[mac] iceGatheringState={pc.iceGatheringState}", flush=True)
    with open(os.path.join(HERE, "answer.txt"), "w") as f:
        json.dump({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}, f)
    print("[mac] answer written to answer.txt", flush=True)

    try:
        got = await asyncio.wait_for(done, timeout=90)
        print(f"[mac] ECHO ROUND-TRIP OK: {got!r}", flush=True)
        await asyncio.sleep(1)
        await pc.close()
        sys.exit(0)
    except Exception as e:
        print(f"[mac] FAILED: {e}", flush=True)
        sys.exit(1)


asyncio.run(main())
