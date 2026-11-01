"""Test dataset for the full benchmark. Same text sent to every provider.
Categories: general (normal speech), technical (numbers/dates), hard (currency,
symbols, emails — used for the Robustness metric)."""

DATASET = {
    "en": [
        {"id": "en_gen_1", "category": "general",   "text": "Thank you for calling Manulife Finance. How may I help you today?"},
        {"id": "en_gen_2", "category": "general",   "text": "I would like to book a consultation with a financial advisor."},
        {"id": "en_tec_1", "category": "technical", "text": "Your policy number is 4821 and the premium is due on March third."},
        {"id": "en_hard_1", "category": "hard",     "text": "Please transfer five thousand dollars to account number 12 345 plus a 2.5 percent fee."},
        {"id": "en_hard_2", "category": "hard",     "text": "Email the report to john dot doe at example dot com before 11:59 PM."},
    ],
    "ja": [
        {"id": "ja_gen_1", "category": "general",   "text": "お電話ありがとうございます。本日はどのようなご用件でしょうか。"},
        {"id": "ja_gen_2", "category": "general",   "text": "ファイナンシャルアドバイザーとの相談を予約したいです。"},
        {"id": "ja_tec_1", "category": "technical", "text": "保険証券番号は四八二一番で、保険料の支払いは三月三日です。"},
        {"id": "ja_hard_1", "category": "hard",     "text": "口座番号十二の三四五に五千ドルを送金し、二点五パーセントの手数料を加えてください。"},
        {"id": "ja_hard_2", "category": "hard",     "text": "レポートを午後十一時五十九分までに送ってください。"},
    ],
}
