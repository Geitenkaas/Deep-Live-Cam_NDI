"""
NDI HX output via the NDI 6 Advanced SDK.

HX mode  : encodes each frame to H.264 with NVENC (PyAV) and sends a
           compressed NDI packet — low bandwidth, hardware-accelerated.
BGRA mode: sends raw BGRA pixels as standard NDI — zero encode latency,
           higher bandwidth.  Used automatically if NVENC is unavailable.
"""

import ctypes
import ctypes.util
import os
import platform
from pathlib import Path
from typing import Optional

import numpy as np

# ── FourCC / frame-format constants ──────────────────────────────────────────
_FOURCC_BGRA  = 0x41524742   # b'BGRA'
_FOURCC_H264  = 0x34363248   # b'H264'  – NDI HX H.264
_FRAME_FORMAT_PROGRESSIVE = 1
# INT64_MIN tells NDI to synthesise its own timecode
_TIMECODE_SYNTHESIZE = ctypes.c_int64(-9223372036854775808).value


# ── C struct mirrors ──────────────────────────────────────────────────────────

class _SendCreate(ctypes.Structure):
    _fields_ = [
        ("p_ndi_name",  ctypes.c_char_p),
        ("p_groups",    ctypes.c_char_p),
        ("clock_video", ctypes.c_bool),
        ("clock_audio", ctypes.c_bool),
        ("_pad",        ctypes.c_uint8 * 6),   # align struct to pointer size
    ]


class _VideoFrameV2(ctypes.Structure):
    # Mirrors NDIlib_video_frame_v2_t (72 bytes on x64).
    # ctypes inserts alignment padding automatically between frame_format_type
    # and timecode (28→32) and between the stride int and p_metadata (52→56).
    _fields_ = [
        ("xres",                              ctypes.c_int),
        ("yres",                              ctypes.c_int),
        ("FourCC",                            ctypes.c_uint32),
        ("frame_rate_N",                      ctypes.c_int),
        ("frame_rate_D",                      ctypes.c_int),
        ("picture_aspect_ratio",              ctypes.c_float),
        ("frame_format_type",                 ctypes.c_int),
        ("timecode",                          ctypes.c_int64),   # +4 pad before
        ("p_data",                            ctypes.c_void_p),
        ("line_stride_or_data_size_in_bytes", ctypes.c_int),
        ("_pad",                              ctypes.c_uint32),  # align next ptr
        ("p_metadata",                        ctypes.c_char_p),
        ("timestamp",                         ctypes.c_int64),
    ]


class _CompressedPacket(ctypes.Structure):
    # Mirrors NDIlib_compressed_packet_t (40 bytes).
    # version must equal sizeof(this struct) = 40.
    _fields_ = [
        ("version",         ctypes.c_uint32),
        ("_pad",            ctypes.c_uint32),   # align int64 to offset 8
        ("pts",             ctypes.c_int64),
        ("dts",             ctypes.c_int64),
        ("flags",           ctypes.c_uint64),   # 1 = keyframe
        ("data_size",       ctypes.c_uint32),
        ("extra_data_size", ctypes.c_uint32),
    ]


_COMPRESSED_PACKET_SIZE = ctypes.sizeof(_CompressedPacket)   # should be 40


# ── DLL discovery ─────────────────────────────────────────────────────────────

def _find_ndi_dll() -> Optional[str]:
    if platform.system() == "Windows":
        roots = [
            r"C:\Program Files\NDI",
            r"C:\Program Files (x86)\NDI",
        ]
        # Prefer Advanced SDK DLL; fall back to standard runtime
        dll_names = [
            "Processing.NDI.Lib.Advanced.x64.dll",
            "Processing.NDI.Lib.x64.dll",
        ]
        for root in roots:
            if not os.path.isdir(root):
                continue
            # Sort descending so "NDI 6 …" beats "NDI 5 …"
            for sdk_dir in sorted(Path(root).iterdir(), reverse=True):
                for dll in dll_names:
                    candidate = sdk_dir / "Bin" / "x64" / dll
                    if candidate.exists():
                        return str(candidate)
        # Last resort: system PATH
        for dll in dll_names:
            found = ctypes.util.find_library(dll)
            if found:
                return found
    else:
        for p in ("/usr/local/lib/libndi_advanced.so", "/usr/lib/libndi_advanced.so",
                  "/usr/local/lib/libndi.so", "/usr/lib/libndi.so"):
            if os.path.exists(p):
                return p
    return None


# ── Sender class ──────────────────────────────────────────────────────────────

class NDISender:
    """
    Usage:
        sender = NDISender("DeepLiveCam", width=960, height=540, fps=30)
        sender.send(bgr_frame)   # numpy uint8 BGR, same shape every call
        sender.close()
    """

    def __init__(self, name: str, width: int, height: int,
                 fps: int = 30, use_hx: bool = True):
        self._width   = width
        self._height  = height
        self._fps     = fps
        self._use_hx  = use_hx
        self._pts     = 0
        self._inst    = None
        self._lib     = None
        self._encoder = None

        dll = _find_ndi_dll()
        if not dll:
            raise RuntimeError(
                "NDI Advanced SDK DLL not found. "
                "Install from https://ndi.video/for-developers/ndi-sdk/"
            )

        self._lib = ctypes.CDLL(dll)
        self._bind()

        if not self._lib.NDIlib_initialize():
            raise RuntimeError("NDIlib_initialize() failed.")

        cfg = _SendCreate(
            p_ndi_name=name.encode(),
            p_groups=None,
            clock_video=False,   # we drive timing ourselves
            clock_audio=False,
        )
        self._inst = self._lib.NDIlib_send_create(ctypes.byref(cfg))
        if not self._inst:
            raise RuntimeError("NDIlib_send_create() failed.")

        if use_hx:
            self._encoder = self._make_encoder()

        mode = "HX H.264 (NVENC)" if (use_hx and self._encoder) else "BGRA (standard NDI)"
        print(f"[NDI] '{name}'  {width}×{height}@{fps}fps  [{mode}]  dll={dll}")

    # ── function binding ──────────────────────────────────────────────────────

    def _bind(self) -> None:
        lib = self._lib
        lib.NDIlib_initialize.restype  = ctypes.c_bool
        lib.NDIlib_initialize.argtypes = []

        lib.NDIlib_send_create.restype  = ctypes.c_void_p
        lib.NDIlib_send_create.argtypes = [ctypes.c_void_p]

        lib.NDIlib_send_send_video_v2.restype  = None
        lib.NDIlib_send_send_video_v2.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        lib.NDIlib_send_destroy.restype  = None
        lib.NDIlib_send_destroy.argtypes = [ctypes.c_void_p]

        lib.NDIlib_destroy.restype  = None
        lib.NDIlib_destroy.argtypes = []

    # ── NVENC encoder ─────────────────────────────────────────────────────────

    def _make_encoder(self):
        try:
            import av
            ctx = av.CodecContext.create("h264_nvenc", "w")
            ctx.width     = self._width
            ctx.height    = self._height
            ctx.pix_fmt   = "yuv420p"
            ctx.framerate = self._fps
            ctx.time_base = f"1/{self._fps}"
            ctx.options   = {
                "preset":      "p1",   # fastest NVENC preset
                "tune":        "ll",   # low-latency tuning
                "zerolatency": "1",
                "bf":          "0",    # no B-frames
                "rc":          "vbr",
            }
            ctx.open()
            return ctx
        except Exception as exc:
            print(f"[NDI] NVENC unavailable ({exc}) — falling back to BGRA.")
            return None

    # ── public send ───────────────────────────────────────────────────────────

    def send(self, bgr_frame: np.ndarray) -> None:
        """Send one BGR frame. Thread-safe (caller must not resize between calls)."""
        if self._use_hx and self._encoder:
            self._send_hx(bgr_frame)
        else:
            self._send_bgra(bgr_frame)

    # ── BGRA path ─────────────────────────────────────────────────────────────

    def _send_bgra(self, bgr: np.ndarray) -> None:
        bgra = np.ascontiguousarray(
            np.concatenate(
                [bgr, np.full((*bgr.shape[:2], 1), 255, dtype=np.uint8)],
                axis=2,
            )
        )
        vf = _VideoFrameV2(
            xres=self._width,
            yres=self._height,
            FourCC=_FOURCC_BGRA,
            frame_rate_N=self._fps * 1000,
            frame_rate_D=1000,
            picture_aspect_ratio=self._width / self._height,
            frame_format_type=_FRAME_FORMAT_PROGRESSIVE,
            timecode=_TIMECODE_SYNTHESIZE,
            p_data=bgra.ctypes.data,
            line_stride_or_data_size_in_bytes=self._width * 4,
            p_metadata=None,
            timestamp=0,
        )
        self._lib.NDIlib_send_send_video_v2(self._inst, ctypes.byref(vf))

    # ── HX / H.264 path ───────────────────────────────────────────────────────

    def _send_hx(self, bgr: np.ndarray) -> None:
        import av

        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        av_frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        av_frame = av_frame.reformat(format="yuv420p")
        av_frame.pts = self._pts
        self._pts += 1

        for pkt in self._encoder.encode(av_frame):
            self._send_compressed(bytes(pkt), bool(pkt.is_keyframe))

    def _send_compressed(self, h264: bytes, keyframe: bool) -> None:
        hdr = _CompressedPacket(
            version=_COMPRESSED_PACKET_SIZE,
            _pad=0,
            pts=self._pts,
            dts=self._pts,
            flags=1 if keyframe else 0,
            data_size=len(h264),
            extra_data_size=0,
        )
        total = _COMPRESSED_PACKET_SIZE + len(h264)
        buf   = (ctypes.c_uint8 * total)()
        ctypes.memmove(buf, ctypes.byref(hdr), _COMPRESSED_PACKET_SIZE)
        ctypes.memmove(ctypes.addressof(buf) + _COMPRESSED_PACKET_SIZE, h264, len(h264))

        vf = _VideoFrameV2(
            xres=self._width,
            yres=self._height,
            FourCC=_FOURCC_H264,
            frame_rate_N=self._fps * 1000,
            frame_rate_D=1000,
            picture_aspect_ratio=self._width / self._height,
            frame_format_type=_FRAME_FORMAT_PROGRESSIVE,
            timecode=_TIMECODE_SYNTHESIZE,
            p_data=ctypes.addressof(buf),
            line_stride_or_data_size_in_bytes=total,
            p_metadata=None,
            timestamp=0,
        )
        self._lib.NDIlib_send_send_video_v2(self._inst, ctypes.byref(vf))

    # ── cleanup ───────────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._encoder:
            try:
                for _ in self._encoder.encode(None):
                    pass
            except Exception:
                pass
        if self._inst and self._lib:
            self._lib.NDIlib_send_destroy(self._inst)
            self._lib.NDIlib_destroy()
        self._inst = None
