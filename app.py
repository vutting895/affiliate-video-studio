from flask import Flask, render_template, request, jsonify
import os
import re
import json
import random
import requests
from google import genai
import edge_tts
from moviepy.editor import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, vfx

app = Flask(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
FAL_KEY = os.getenv("FAL_KEY", "")

OUTPUT_DIR = "./static/outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs("./raw_footages", exist_ok=True)

def generate_gemini_script(product_title: str) -> dict:
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
    สินค้า: {product_title}
    หน้าที่ของคุณ:
    1. เขียนพาดหัวตัวโตตื่นเต้น สั้นๆ สำหรับวางบนคลิป (ไม่เกิน 6 คำ)
    2. เขียนบทพากย์ป้ายยาภาษาไทย ความยาว 15 วินาที กระชับ น่าซื้อ

    ตอบกลับเป็นรูปแบบ JSON เท่านั้น โดยไม่ต้องมีข้อความอื่น:
    {{
        "headline": "...",
        "script": "..."
    }}
    """
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
    )
    
    text_res = response.text.strip()
    if text_res.startswith("```json"):
        text_res = text_res[7:-3].strip()
    elif text_res.startswith("```"):
        text_res = text_res[3:-3].strip()
        
    return json.loads(text_res)

async def generate_voiceover(text: str, output_path: str):
    communicate = edge_tts.Communicate(text, "th-TH-PremwadeeNeural", rate="+8%")
    await communicate.save(output_path)

def generate_fal_video(image_url: str, product_title: str, fal_key: str) -> str:
    output_path = os.path.join("./raw_footages", "temp_render_raw.mp4")
    
    if fal_key and image_url:
        headers = {
            "Authorization": f"Key {fal_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "prompt": f"Cinematic 3D product showcase of {product_title}, subtle camera pan, high quality studio lighting, 9:16 vertical orientation",
            "image_url": image_url,
            "aspect_ratio": "9:16"
        }
        api_url = "https://fal.run/fal-ai/minimax/video-01/image-to-video"

        try:
            res = requests.post(api_url, headers=headers, json=payload, timeout=120)
            if res.status_code == 200:
                data = res.json()
                video_url = data.get("video", {}).get("url") or data.get("video_file", {}).get("url")
                if video_url:
                    v_bytes = requests.get(video_url).content
                    with open(output_path, "wb") as f:
                        f.write(v_bytes)
                    return output_path
        except Exception as e:
            print(f"⚠️ [Fal.ai Error] {e}")

    files = [os.path.join("./raw_footages", f) for f in os.listdir("./raw_footages") if f.endswith(('.mp4', '.mov'))]
    return random.choice(files) if files else None

def render_anti_shadowban_video(raw_video_path: str, audio_path: str, headline: str, output_path: str):
    audio = AudioFileClip(audio_path)
    video = VideoFileClip(raw_video_path)

    if video.duration < audio.duration:
        video = video.loop(duration=audio.duration)
    else:
        video = video.subclip(0, audio.duration)

    video = video.fx(vfx.speedx, 1.02).resize(height=1920)
    if video.w > 1080:
        video = video.crop(x_center=video.w / 2, width=1080)

    crop_offset = random.randint(5, 12)
    video = video.crop(
        x1=crop_offset, y1=crop_offset, 
        x2=video.w - crop_offset, y2=video.h - crop_offset
    ).resize((1080, 1920))

    txt_clip = TextClip(
        headline, fontsize=52, color='yellow', bg_color='black',
        font='Tahoma', method='caption', size=(950, None)
    ).set_position(('center', 200)).set_duration(audio.duration)

    final_clip = CompositeVideoClip([video.set_audio(audio), txt_clip])
    final_clip.write_videofile(
        output_path, fps=30, codec='libx264', audio_codec='aac', preset='ultrafast'
    )

    audio.close()
    video.close()
    final_clip.close()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/generate", methods=["POST"])
def generate():
    data = request.json
    user_input = data.get("url", "")
    
    url_match = re.search(r'https?://[^\s]+', user_input)
    if not url_match:
        return jsonify({"success": False, "error": "กรุณากรอกลิงก์สินค้าที่ถูกต้อง"})
    
    affiliate_url = url_match.group(0)
    clean_title = re.sub(r'https?://[^\s]+', '', user_input).strip()
    product_title = clean_title if clean_title else "สินค้าราคาพิเศษลดใหญ่ประจำวัน"

    try:
        ai_data = generate_gemini_script(product_title)
        default_img = "https://images.pexels.com/photos/3780104/pexels-photo-3780104.jpeg"
        raw_video = generate_fal_video(default_img, product_title, FAL_KEY)
        if not raw_video:
            return jsonify({"success": False, "error": "ไม่สามารถสร้างวิดีโอดิบจาก Fal.ai ได้"})

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(generate_voiceover(ai_data['script'], "temp_voice.mp3"))

        output_filename = f"video_{random.randint(1000,9999)}.mp4"
        output_path = os.path.join(OUTPUT_DIR, output_filename)
        
        render_anti_shadowban_video(raw_video, "temp_voice.mp3", ai_data['headline'], output_path)

        return jsonify({
            "success": True,
            "headline": ai_data['headline'],
            "script": ai_data['script'],
            "video_url": f"/static/outputs/{output_filename}",
            "affiliate_url": affiliate_url
        })

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"success": False, "error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=True)
