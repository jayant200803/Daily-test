"""Day-1 Atlas test settings. All endpoints/fields follow the Atlas docs."""
BASE_URL   = "https://atlas.oneinbox.ai"
LISTEN_MODEL = "atlas-listen"
SPEAK_MODEL  = "atlas-speak"

# --- Speak (TTS) test inputs (defaults; edit freely) ---
SPEAK_VOICE = "souta"                      # default voice per docs
# The 12 Japanese voices atlas-speak offers (Speak > Voices). Set SPEAK_VOICE above
# to any of these. English text is spoken with a Japanese voice (per docs).
SPEAK_VOICES = ["ayaka", "emi", "kaito", "sakura", "souta", "nanami",
                "haruto", "mei", "yuto", "rina", "ren", "hajun"]
ENGLISH_TEXTS = [
    "Thank you for calling. How may I help you with your booking today?",
    "Your flight has been changed to NS1156 at 20:30, and you are saving 60 euros.",
]
# Latency pass target for time-to-first-audio (ms). Report-only if None.
FIRST_AUDIO_TARGET_MS = 1000

# --- Pronunciation dictionary test ---
# The dictionary lives on your KEY (docs). We cannot read it, only hear it.
# Put words your key has custom readings for; leave empty to skip the test.
PRON_WORDS = []            # e.g. ["OneInbox", "Exotel"]

# --- Concurrency test ---
CONCURRENCY_ATTEMPT = 6    # open this many at once; expect the limit+1 to be refused
