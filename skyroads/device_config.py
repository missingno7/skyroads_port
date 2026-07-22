"""Pure deterministic device selection shared by every execution carrier."""


def capture_sound_blaster_pcm(args) -> bool:
    """Whether the selected runtime exposes PCM to the host presentation."""
    return (
        not bool(args.no_sound)
        and str(getattr(args, "audio", "off")) in {
            "adlib", "native-faithful", "native-stereo",
        }
        and not bool(args.headless)
    )
