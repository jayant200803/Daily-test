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

# --- Day-2 speech-to-speech pipeline: the LLM in the middle ---
# Atlas has NO LLM (chat 404s), so the "reply" step uses a separate OpenAI-compatible
# provider. Default = Groq (free, fast). To switch providers, change these three lines.
LLM_BASE_URL = "https://api.groq.com/openai/v1"   # OpenAI-compatible base
LLM_MODEL    = "openai/gpt-oss-20b"               # a model this key can use
LLM_KEY_ENV  = "GROQ_API_KEY"                      # env var holding the LLM key
ASSISTANT_SYSTEM = (
    "You are a friendly, concise voice assistant for a travel booking call center. "
    "Your ONLY job is helping the caller book a trip (flights/travel): collecting origin, "
    "destination, dates, cabin class, passenger details and confirming the booking. "
    "Use the conversation so far and remember details already given — never ask again for "
    "something already provided. Move the booking forward: confirm what you have and ask only "
    "for the missing piece. "
    "STRICT RULE: if the caller asks about anything NOT related to booking a trip (general "
    "knowledge, math, jokes, poems, coding, weather, news, personal advice, etc.), do NOT "
    "answer it. Politely say you can only help with travel bookings and steer back, e.g. "
    "'I can only help with travel bookings. Shall we continue with your trip?' "
    "FORMAT RULES (important): reply with ONLY your single next line as the agent — ONE short "
    "sentence, at most about 20 words. Never write the caller's words, never produce multiple "
    "questions or multiple turns in one reply, and ask for only ONE piece of missing info at a "
    "time. No lists, markdown, or emojis."
)
