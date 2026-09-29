import os
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
from google import genai
from google.genai import types

load_dotenv()
video_id = "BZBoXGhlXSE"

print("--- 1. Trying YouTubeTranscriptApi ---")
try:
    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
    for t in transcript_list:
        print(f"Found transcript: {t.language} ({t.language_code}), is_generated: {t.is_generated}")
    transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['uz', 'ru', 'en'])
    text = " ".join([item['text'] for item in transcript])
    print(f"Transcript snippet (len {len(text)}): {text[:500]}...")
    with open("video_transcript.txt", "w", encoding="utf-8") as f:
        f.write(text)
    print("Saved to video_transcript.txt")
except Exception as e:
    print(f"YouTubeTranscriptApi error: {e}")

print("\n--- 2. Testing Gemini direct YouTube understanding ---")
try:
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    prompt = (
        "Ushbu videoni tahlil qil va Muhammadali Eshonqulov aytgan Yuksalish maktablari tizimi, "
        "uning falsafasi, tarbiya va ta'lim metodikasi, o'ziga xosligi va asosiy ustunliklari haqidagi barcha "
        "muhim ma'lumotlarni batafsil punktlar ko'rinishida o'zbek tilida chiqarib ber."
    )
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=[
            types.Part.from_uri(
                file_uri="https://www.youtube.com/watch?v=BZBoXGhlXSE",
                mime_type="video/*"
            ),
            prompt
        ]
    )
    print(f"Gemini response snippet: {response.text[:500]}...")
    with open("gemini_video_summary.txt", "w", encoding="utf-8") as f:
        f.write(response.text)
    print("Saved to gemini_video_summary.txt")
except Exception as e:
    print(f"Gemini direct error: {e}")
