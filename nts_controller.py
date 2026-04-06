"""NTS Radio WiFi Speaker Controller — backend logic.

Handles:
- NTS live channel and mixtape metadata from the NTS API
- SSH-based mpv playback control on Raspberry Pi speakers via Paramiko
- YouTube / YouTube Music URL resolution via yt-dlp
"""

import json
import logging
import os
import subprocess
from pathlib import Path

import paramiko
import requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "nts.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Speaker config
# ---------------------------------------------------------------------------

SPEAKERS_PATH = Path(__file__).parent / "speakers.json"

NTS_STREAM_URLS = {
    "1": "https://stream-relay-geo.ntslive.net/stream",
    "2": "https://stream-relay-geo.ntslive.net/stream2",
}

NTS_LIVE_API = "https://www.nts.live/api/v2/live"
NTS_MIXTAPES_API = "https://www.nts.live/api/v2/mixtapes"

SSH_KEY_PATH = os.path.expanduser("~/.ssh/id_ed25519")
SSH_TIMEOUT = 10  # seconds


def load_speakers() -> list[dict]:
    with open(SPEAKERS_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# NTS API
# ---------------------------------------------------------------------------


def fetch_live_channels() -> list[dict]:
    """Return metadata for both live NTS channels.

    Each dict has: channel_name, broadcast_title, description, artwork_url, stream_url.
    """
    try:
        resp = requests.get(NTS_LIVE_API, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("Failed to fetch NTS live channels: %s", exc)
        raise

    channels = []
    for item in data.get("results", []):
        channel_id = item.get("channel_name", "").replace("NTS ", "")
        stream_url = NTS_STREAM_URLS.get(channel_id, NTS_STREAM_URLS["1"])

        now = item.get("now", {})
        embeds = now.get("embeds", {})
        details = embeds.get("details", {})
        artwork_url = (
            (details.get("media", {}) or {})
            .get("picture_medium_large", "")
        )

        channels.append(
            {
                "channel_name": item.get("channel_name", f"Channel {channel_id}"),
                "broadcast_title": now.get("broadcast_title", ""),
                "description": details.get("description", ""),
                "artwork_url": artwork_url,
                "stream_url": stream_url,
            }
        )

    return channels


def fetch_mixtapes() -> list[dict]:
    """Return NTS Infinite Mixtapes.

    Each dict has: name, description, stream_url, artwork_url.
    """
    try:
        resp = requests.get(NTS_MIXTAPES_API, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("Failed to fetch NTS mixtapes: %s", exc)
        raise

    mixtapes = []
    for item in data.get("results", []):
        mixtapes.append(
            {
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "stream_url": item.get("audio_stream_endpoint", ""),
                "artwork_url": item.get("media", {}).get("picture_medium_large", ""),
            }
        )

    return mixtapes


# ---------------------------------------------------------------------------
# SSH / mpv control
# ---------------------------------------------------------------------------


def _ssh_exec(host: str, user: str, command: str) -> tuple[str, str, int]:
    """Open an SSH connection, run a command, return (stdout, stderr, exit_code)."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            username=user,
            key_filename=SSH_KEY_PATH,
            timeout=SSH_TIMEOUT,
            banner_timeout=SSH_TIMEOUT,
        )
        _, stdout, stderr = client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        return out, err, exit_code
    finally:
        client.close()


def _mpv_command(stream_url: str, audio_device: str | None = None) -> str:
    """Build the shell command to start mpv on the Pi."""
    device_flag = f" --audio-device={audio_device}" if audio_device else ""
    return (
        f"pkill -f mpv; "
        f"nohup mpv --no-video --really-quiet{device_flag} "
        f"'{stream_url}' </dev/null >/dev/null 2>&1 &"
    )


def play_stream(speaker: dict, stream_url: str) -> None:
    """SSH to the Pi and start playing stream_url with mpv."""
    host = speaker["host"]
    user = speaker["ssh_user"]
    audio_device = speaker.get("audio_device")
    cmd = _mpv_command(stream_url, audio_device)
    log.info("play_stream: %s -> %s [%s]", speaker["name"], stream_url, host)
    out, err, code = _ssh_exec(host, user, cmd)
    if err:
        log.warning("play_stream stderr (%s): %s", speaker["name"], err)


def stop_stream(speaker: dict) -> None:
    """SSH to the Pi and kill any running mpv process."""
    host = speaker["host"]
    user = speaker["ssh_user"]
    log.info("stop_stream: %s [%s]", speaker["name"], host)
    _ssh_exec(host, user, "pkill -f mpv")


def get_status(speaker: dict) -> str | None:
    """Return the command line of the running mpv process, or None if idle."""
    host = speaker["host"]
    user = speaker["ssh_user"]
    out, _, code = _ssh_exec(host, user, "pgrep -fa mpv")
    return out if code == 0 and out else None


# ---------------------------------------------------------------------------
# YouTube / YouTube Music via mpv + yt-dlp (Pi-side resolution)
# ---------------------------------------------------------------------------


def is_playlist_url(url: str) -> bool:
    """Return True if the URL refers to a YouTube playlist rather than a single track."""
    return "list=" in url or "/playlist" in url


def fetch_playlist_tracks(yt_url: str) -> list[dict]:
    """Fetch playlist track metadata from YouTube without downloading audio.

    Runs yt-dlp --flat-playlist -j on the server. Returns a list of dicts:
        title        (str) — track title
        duration_str (str) — formatted as M:SS, or "?" if unknown
        yt_url       (str) — individual track URL
    """
    log.info("fetch_playlist_tracks: %s", yt_url)
    try:
        result = subprocess.run(
            ["yt-dlp", "--flat-playlist", "-j", "--no-warnings", yt_url],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise RuntimeError("yt-dlp is not installed. Run: pip install yt-dlp")
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp timed out fetching playlist metadata.")

    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")

    tracks = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        title = entry.get("title") or entry.get("id", "Unknown")
        duration = entry.get("duration")
        if duration is not None:
            mins, secs = divmod(int(duration), 60)
            duration_str = f"{mins}:{secs:02d}"
        else:
            duration_str = "?"

        track_url = entry.get("url") or entry.get("webpage_url")
        if not track_url:
            vid_id = entry.get("id")
            track_url = f"https://www.youtube.com/watch?v={vid_id}" if vid_id else yt_url

        tracks.append({"title": title, "duration_str": duration_str, "yt_url": track_url})

    if not tracks:
        raise RuntimeError("No tracks found in playlist. Is the URL correct?")

    log.info("fetch_playlist_tracks: found %d tracks", len(tracks))
    return tracks


def play_yt(speaker: dict, yt_url: str) -> None:
    """Play a YouTube or YouTube Music URL (single track or playlist) on the Pi.

    The URL is passed directly to mpv, which resolves it lazily via its built-in
    yt-dlp hook. Works for single tracks and playlists identically.

    Requires yt-dlp installed on the Pi: pip install yt-dlp
    """
    host, user = speaker["host"], speaker["ssh_user"]
    cmd = _mpv_command(yt_url, speaker.get("audio_device"))
    log.info("play_yt: %s -> %s [%s]", speaker["name"], yt_url, host)
    out, err, _ = _ssh_exec(host, user, cmd)
    if err:
        log.warning("play_yt stderr (%s): %s", speaker["name"], err)
