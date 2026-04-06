"""NTS Radio WiFi Speaker Controller — Streamlit dashboard."""

import time

import streamlit as st

import nts_controller as nts

st.set_page_config(page_title="NTS Speaker Control", page_icon="📻", layout="wide")

# ---------------------------------------------------------------------------
# Cached data fetching (60-second TTL)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=60)
def get_live_channels():
    return nts.fetch_live_channels()


@st.cache_data(ttl=60)
def get_mixtapes():
    return nts.fetch_mixtapes()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def speaker_names(speakers: list[dict]) -> list[str]:
    return [s["name"] for s in speakers]


def find_speaker(speakers: list[dict], name: str) -> dict:
    return next(s for s in speakers if s["name"] == name)


def do_play(speaker: dict, stream_url: str, label: str) -> None:
    with st.spinner(f"Connecting to {speaker['name']}…"):
        try:
            nts.play_stream(speaker, stream_url)
            st.success(f"Playing {label} on {speaker['name']}")
        except Exception as exc:
            st.error(f"Failed: {exc}")



def do_stop(speaker: dict) -> None:
    with st.spinner(f"Stopping {speaker['name']}…"):
        try:
            nts.stop_stream(speaker)
            st.success(f"Stopped {speaker['name']}")
        except Exception as exc:
            st.error(f"Failed: {exc}")


# ---------------------------------------------------------------------------
# Main layout
# ---------------------------------------------------------------------------

speakers = nts.load_speakers()
names = speaker_names(speakers)

# Header
col_title, col_refresh = st.columns([6, 1])
with col_title:
    st.title("NTS Speaker Control")
with col_refresh:
    st.write("")
    if st.button("Refresh", use_container_width=True):
        get_live_channels.clear()
        get_mixtapes.clear()
        st.rerun()

st.caption(f"Last loaded: {time.strftime('%H:%M:%S')}")

st.divider()

# ---------------------------------------------------------------------------
# Live Channels
# ---------------------------------------------------------------------------

st.subheader("Live Channels")

try:
    channels = get_live_channels()
except Exception as exc:
    st.error(f"Could not load NTS live channels: {exc}")
    channels = []

if channels:
    cols = st.columns(len(channels))
    for col, ch in zip(cols, channels):
        with col:
            if ch["artwork_url"]:
                st.image(ch["artwork_url"], use_container_width=True)
            st.markdown(f"**{ch['channel_name']}**")
            st.markdown(f"*{ch['broadcast_title']}*")
            if ch["description"]:
                st.caption(ch["description"][:120] + ("…" if len(ch["description"]) > 120 else ""))
            selected = st.selectbox(
                "Speaker", names, key=f"ch_speaker_{ch['channel_name']}"
            )
            if st.button("Play", key=f"ch_play_{ch['channel_name']}", use_container_width=True):
                do_play(find_speaker(speakers, selected), ch["stream_url"], ch["channel_name"])

st.divider()

# ---------------------------------------------------------------------------
# Infinite Mixtapes
# ---------------------------------------------------------------------------

st.subheader("Infinite Mixtapes")

try:
    mixtapes = get_mixtapes()
except Exception as exc:
    st.error(f"Could not load NTS mixtapes: {exc}")
    mixtapes = []

if mixtapes:
    COLS = 3
    for row_start in range(0, len(mixtapes), COLS):
        row = mixtapes[row_start : row_start + COLS]
        cols = st.columns(COLS)
        for col, mix in zip(cols, row):
            with col:
                if mix["artwork_url"]:
                    st.image(mix["artwork_url"], use_container_width=True)
                st.markdown(f"**{mix['name']}**")
                if mix["description"]:
                    st.caption(mix["description"][:100] + ("…" if len(mix["description"]) > 100 else ""))
                selected = st.selectbox(
                    "Speaker", names, key=f"mix_speaker_{mix['name']}"
                )
                if mix["stream_url"]:
                    if st.button("Play", key=f"mix_play_{mix['name']}", use_container_width=True):
                        do_play(find_speaker(speakers, selected), mix["stream_url"], mix["name"])

st.divider()

# ---------------------------------------------------------------------------
# YouTube / YouTube Music
# ---------------------------------------------------------------------------

st.subheader("YouTube / YouTube Music")
st.caption(
    "Paste a YouTube or YouTube Music URL — single track or full playlist. "
    "For playlists, click Preview to see the track list before playing."
)

yt_url = st.text_input(
    "YouTube URL",
    placeholder=(
        "https://music.youtube.com/playlist?list=… or "
        "https://www.youtube.com/watch?v=…"
    ),
    label_visibility="collapsed",
    key="yt_url_input",
)
yt_speaker_name = st.selectbox("Speaker", names, key="yt_speaker")

# Clear stored playlist preview if the URL has changed
stripped_url = yt_url.strip()
if stripped_url != st.session_state.get("yt_playlist_url", ""):
    st.session_state.pop("yt_playlist", None)
    st.session_state.pop("yt_playlist_url", None)

if stripped_url and nts.is_playlist_url(stripped_url):
    # Playlist flow: preview first, then play
    col_preview, col_play = st.columns([1, 2])

    with col_preview:
        if st.button("Preview tracks", key="yt_preview", use_container_width=True):
            with st.spinner("Fetching playlist…"):
                try:
                    tracks = nts.fetch_playlist_tracks(stripped_url)
                    st.session_state["yt_playlist"] = tracks
                    st.session_state["yt_playlist_url"] = stripped_url
                except Exception as exc:
                    st.error(f"Could not fetch playlist: {exc}")

    if "yt_playlist" in st.session_state:
        tracks = st.session_state["yt_playlist"]
        DISPLAY_LIMIT = 50
        st.markdown(f"**{len(tracks)} tracks**")
        for i, track in enumerate(tracks[:DISPLAY_LIMIT], 1):
            st.markdown(f"{i}. {track['title']}  `{track['duration_str']}`")
        if len(tracks) > DISPLAY_LIMIT:
            st.caption(f"…and {len(tracks) - DISPLAY_LIMIT} more")

        with col_play:
            if st.button(
                f"Play all on {yt_speaker_name}",
                key="yt_play_playlist",
                use_container_width=True,
                type="primary",
            ):
                speaker = find_speaker(speakers, yt_speaker_name)
                with st.spinner(f"Starting playlist on {speaker['name']}…"):
                    try:
                        nts.play_yt(speaker, stripped_url)
                        st.success(f"Playing {len(tracks)}-track playlist on {speaker['name']}")
                    except Exception as exc:
                        st.error(f"Failed: {exc}")
else:
    # Single track flow
    if st.button("Play", key="yt_play", use_container_width=False):
        if not stripped_url:
            st.warning("Paste a YouTube URL first.")
        else:
            speaker = find_speaker(speakers, yt_speaker_name)
            with st.spinner(f"Connecting to {speaker['name']}…"):
                try:
                    nts.play_yt(speaker, stripped_url)
                    st.success(f"Playing on {speaker['name']}")
                except Exception as exc:
                    st.error(f"Failed: {exc}")

st.divider()

# ---------------------------------------------------------------------------
# Speaker Status
# ---------------------------------------------------------------------------

st.subheader("Speaker Status")
st.info(
    "If casting Spotify to a speaker, stop any NTS/YouTube stream first — "
    "both use the same audio output and will conflict.",
    icon="ℹ️",
)

for speaker in speakers:
    col_name, col_status, col_stop = st.columns([2, 5, 1])
    with col_name:
        st.markdown(f"**{speaker['name']}**")
    with col_status:
        try:
            status = nts.get_status(speaker)
            if status:
                st.markdown(f"`{status}`")
            else:
                st.markdown("*idle*")
        except Exception as exc:
            st.markdown(f"*unreachable: {exc}*")
    with col_stop:
        if st.button("Stop", key=f"stop_{speaker['name']}", use_container_width=True):
            do_stop(speaker)
