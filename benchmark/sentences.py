"""Fixed test sentences for the benchmark. The SAME text is sent to every provider
so the comparison is fair. A mix of finance phrasing + names/numbers (which stress
recognition and pronunciation the most)."""

ENGLISH = [
    "Thank you for calling Manulife Finance, how may I help you today?",
    "I would like to transfer five thousand dollars to my savings account.",
    "Can you tell me about term life insurance and medical coverage?",
    "My policy number is one two three four five six, and my name is Jayant.",
]

JAPANESE = [
    "お電話ありがとうございます。本日はどのようなご用件でしょうか。",
    "医療保険と生命保険について詳しく教えてください。",
    "来月の保険料の支払い方法を変更したいのですが。",
    "担当者に相談の予約をお願いできますか。",
]
