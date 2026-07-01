import gradio as gr
import whisper
import os
import subprocess
import cv2
import asyncio
import edge_tts
import shutil
import time
import re
import json
from datetime import datetime

# =====================================================================
# 🔐 APP CONFIGURATION (သက်တမ်းနှင့် LOGIN ချိန်ညှိရန်)
# =====================================================================
EXPIRE_DATE_STR = "2026-06-30"  # 📅 App သက်တမ်းကုန်မည့်ရက်စွဲ
USER_CREDENTIALS = {
    "admin": "password123",     # 👤 Admin: အကန့်အသတ်မရှိ သုံးနိုင်သည်
    "user1": "123456"           # 👤 User1: လူတိုင်း ဒီအကောင့်နဲ့ ဝင်သုံးပြီး စက်အလိုက် ကန့်သတ်မည်
}

TRACKER_FILE = "usage_tracker.json"
# =====================================================================

def check_app_validity():
    try:
        expire_date = datetime.strptime(EXPIRE_DATE_STR, "%Y-%m-%d").date()
        current_date = datetime.now().date()
        if current_date > expire_date:
            print("\n" + "="*60)
            print(f"❌ APP EXPIRED: This application expired on {EXPIRE_DATE_STR}!")
            print("Please renew your license or adjust your system date to access.")
            print("="*60 + "\n")
            return False
        return True
    except Exception as e:
        print(f"Date check error: {e}")
        return False

print("Loading Lightweight Whisper Tiny Model...")
try:
    model = whisper.load_model("tiny")
except Exception as e:
    print(f"Model load error, retrying with cpu: {e}")
    model = whisper.load_model("tiny", device="cpu")

def text_to_myanmar(text):
    from urllib.parse import quote
    import urllib.request
    import json
    if not text.strip():
        return ""
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=my&dt=t&q={quote(text)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        response = urllib.request.urlopen(req, timeout=5).read().decode('utf-8')
        result = json.loads(response)
        translated = result[0][0][0]
        if translated:
            return translated
    except Exception as e:
        print(f"[API WARNING] Translation Failed/Blocked: {e}")
    return ""

async def generate_voice_segment_high_quality(text, voice_gender, target_duration_sec, filename):
    if voice_gender == "မိန်းကလေးသံ (Nilar)":
        voice = "my-MM-NilarNeural"
        estimated_raw_len_sec = ((len(text) * 150) + 400) / 1000.0
        gender_offset = 0.0
    else:
        voice = "my-MM-ThihaNeural"
        estimated_raw_len_sec = ((len(text) * 160) + 400) / 1000.0
        gender_offset = -0.0

    if target_duration_sec > 0:
        speed_factor = (estimated_raw_len_sec / target_duration_sec) + gender_offset
    else:
        speed_factor = 1.0 + gender_offset
        
    speed_percentage = int((speed_factor - 1.0) * 100)
    speed_percentage = max(-50, min(100, speed_percentage))
    
    sign = "+" if speed_percentage >= 0 else ""
    rate_str = f"{sign}{speed_percentage}%"
    
    communicate = edge_tts.Communicate(text, voice, rate=rate_str)
    await communicate.save(filename)

def load_usage_tracker():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_usage_tracker(tracker_data):
    try:
        with open(TRACKER_FILE, "w") as f:
            json.dump(tracker_data, f, indent=4)
    except Exception as e:
        print(f"Tracker save error: {e}")

def check_user_limit(username, request: gr.Request):
    if username == "admin":
        return True, "Success"
        
    client_ip = request.client.host
    user_agent = request.headers.get("user-agent", "unknown")
    device_id = f"{client_ip}_{user_agent}"
        
    today = str(datetime.now().date())
    usage_tracker = load_usage_tracker()
    
    if device_id not in usage_tracker:
        usage_tracker[device_id] = {"last_date": today, "count": 0}
        
    ip_status = usage_tracker[device_id]
    if ip_status["last_date"] != today:
        ip_status["last_date"] = today
        ip_status["count"] = 0
        
    if ip_status["count"] >= 1:
        return False, f"⚠️ သင့်စက်အတွက် ဒီနေ့ ၁ ကြိမ် Limit ပြည့်သွားပါပြီ။ မနက်ဖြန်မှ ထပ်မံကြိုးစားပါ။"
        
    ip_status["count"] += 1
    save_usage_tracker(usage_tracker)
    return True, "Success"

def process_tiktok_video(video_path, voice_gender, speed, blur_y_percent, request: gr.Request):
    if not video_path:
        return None
        
    # --- 🔐 USER DEVICE LIMIT CHECK ---
    is_allowed, error_message = check_user_limit(request.username, request)
    if not is_allowed:
        raise gr.Error(error_message)

    temp_dir = "temp_sync_segments"
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir, exist_ok=True)
    
    speed_video_path = os.path.join(temp_dir, "speed_video.mp4")
    
    try:
        print("အဆင့် ၁: AI က စကားပြော စက္ကန့်အချိန်တွေကို ဖတ်နေသည်...")
        result = model.transcribe(video_path, language="en")
        segments = result.get('segments', [])
        full_transcription = result.get("text", "").strip()
        
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        video_duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
        
        if not segments or not full_transcription:
            print("[FALLBACK] Non-speech video detected. Generating fallback timelines...")
            fallback_text = "ဒီဗီဒီယိုလေးကို အဆုံးထိ ဆက်ကြည့်ပေးပါဦးဗျာ။ ကြိုက်နှစ်သက်ရင်လည်း Like and Share လုပ်ပေးခဲ့ပါဦး။"
            segments = [{'start': 0.0, 'end': min(6.0, video_duration), 'text': fallback_text}]

        print("အဆင့် ၂: ဗီဒီယိုကို Copyright ကျော်ရန် ဘယ်ညာလှန်ပြီး Speed မြှင့်နေသည်...")
        crop_w, crop_h = int(width * 0.96), int(height * 0.96)
        x_start, y_start = (width - crop_w) // 2, (height - crop_h) // 2
        
        temp_video = "temp_flipped_video.mp4"
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(temp_video, fourcc, fps, (crop_w, crop_h))
        
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            frame = frame[y_start:y_start+crop_h, x_start:x_start+crop_w]
            frame = cv2.flip(frame, 1)
            frame = cv2.convertScaleAbs(frame, alpha=1.03, beta=6)
            out.write(frame)
        cap.release()
        out.release()

        blur_x = int(crop_w * 0.1)
        blur_w = int(crop_w * 0.8)
        blur_h = int(crop_h * 0.12)
        blur_y = int(crop_h * (blur_y_percent / 100))
        
        video_pts_ratio = 1.0 / speed
        v_speed_cmd = [
            'ffmpeg', '-y', '-i', temp_video,
            '-vf', f"delogo=x={blur_x}:y={blur_y}:w={blur_w}:h={blur_h},setpts={video_pts_ratio}*PTS",
            '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
            speed_video_path
        ]
        subprocess.run(v_speed_cmd, check=True)
        
        final_video_duration_ms = int((video_duration / speed) * 1000)

        print("အဆင့် ၃: မြန်မာအသံများကို ထုတ်ယူနေသည်...")
        audio_segments = []
        current_timeline_ms = 0
        
        for idx, seg in enumerate(segments):
            eng_text = seg.get('text', '').strip()
            start_time = seg.get('start', 0.0)
            end_time = seg.get('end', 0.0)
            
            if not eng_text:
                continue
            
            mm_text = text_to_myanmar(eng_text)
            if not mm_text:
                mm_text = eng_text 
            
            original_duration_sec = end_time - start_time
            if original_duration_sec <= 0:
                original_duration_sec = 2.0
                
            target_duration_sec = original_duration_sec / speed
            adjusted_start_ms = int((start_time / speed) * 1000)
            
            high_quality_seg_filename = os.path.join(temp_dir, f"hq_seg_{idx}.mp3")
            time.sleep(0.5) 
            
            try:
                asyncio.run(generate_voice_segment_high_quality(mm_text, voice_gender, target_duration_sec, high_quality_seg_filename))
            except Exception as voice_err:
                print(f"Segment {idx} voice generation failed: {voice_err}")
            
            if os.path.exists(high_quality_seg_filename) and os.path.getsize(high_quality_seg_filename) > 0:
                if adjusted_start_ms > current_timeline_ms:
                    silence_duration_ms = adjusted_start_ms - current_timeline_ms
                    silence_filename = os.path.join(temp_dir, f"silence_before_{idx}.mp3")
                    
                    silence_cmd = [
                        'ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
                        '-t', f'{silence_duration_ms / 1000.0}', silence_filename
                    ]
                    subprocess.run(silence_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    
                    if os.path.exists(silence_filename):
                        audio_segments.append(silence_filename)
                        current_timeline_ms = adjusted_start_ms
                
                audio_segments.append(high_quality_seg_filename)
                current_timeline_ms += int(target_duration_sec * 1000)

        if current_timeline_ms < final_video_duration_ms:
            remaining_silence_ms = final_video_duration_ms - current_timeline_ms
            end_silence_filename = os.path.join(temp_dir, "silence_end.mp3")
            
            end_silence_cmd = [
                'ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
                '-t', f'{remaining_silence_ms / 1000.0}', end_silence_filename
            ]
            subprocess.run(end_silence_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(end_silence_filename):
                audio_segments.append(end_silence_filename)

        merged_audio_path = os.path.join(temp_dir, "final_myanmar_speech.mp3")
        
        if audio_segments:
            concat_list_path = os.path.join(temp_dir, "concat_list.txt")
            with open(concat_list_path, "w", encoding="utf-8") as f:
                for audio_file in audio_segments:
                    f.write(f"file '{os.path.abspath(audio_file)}'\n")
            
            concat_cmd = [
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                '-i', concat_list_path, '-c:a', 'libmp3lame', '-b:a', '192k', merged_audio_path
            ]
            subprocess.run(concat_cmd, check=True)
            has_audio = True
        else:
            has_audio = False

        print("အဆင့် ၄: ဗီဒီယိုနှင့် မြန်မာအသံကို ပေါင်းစပ်နေသည်...")
        output_video_path = "tiktok_ready_voiceover.mp4"
        if os.path.exists(output_video_path):
            os.remove(output_video_path)
            
        if has_audio and os.path.exists(merged_audio_path):
            ffmpeg_cmd = [
                'ffmpeg', '-y', '-i', speed_video_path, '-i', merged_audio_path,
                '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-shortest',
                output_video_path
            ]
        else:
            ffmpeg_cmd = [
                'ffmpeg', '-y', '-i', speed_video_path, '-c:v', 'copy', '-an',
                output_video_path
            ]
        subprocess.run(ffmpeg_cmd, check=True)
        
        if os.path.exists(temp_video):
            os.remove(temp_video)
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            
        print("အောင်မြင်စွာ ပြီးဆုံးပါပြီ!")
        return output_video_path
        
    except Exception as e:
        print(f"Error occurred in outer process: {e}")
        final_fallback = "tiktok_fallback_video.mp4"
        if os.path.exists(speed_video_path):
            shutil.copy(speed_video_path, final_fallback)
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            return final_fallback
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        return video_path

# --- 🎨 UI ပိုင်း ပြင်ဆင်မှု ---
custom_css = """
.generate-btn {
    background: linear-gradient(90deg, #FE2C55 0%, #25F4EE 100%) !important;
    color: white !important;
    font-weight: bold !important;
    border: none !important;
    transition: transform 0.2s ease;
}
.generate-btn:hover {
    transform: scale(1.02);
}
.app-header {
    text-align: center;
    padding: 10px 0;
    margin-bottom: 10px;
}
.app-header h1 {
    font-size: 24px !important;
    margin-bottom: 5px !important;
    font-weight: 800;
    background: linear-gradient(45deg, #FE2C55, #25F4EE);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
"""

with gr.Blocks(theme=gr.themes.Soft(), css=custom_css) as demo:
    gr.HTML(
        """
        <div class="app-header">
            <h1>🎬 TikTok AI Myanmar Voiceover Only</h1>
        </div>
        """
    )
        
    with gr.Row():
        with gr.Column(scale=1):
            video_input = gr.Video(
                label="📥 ရွေးချယ်ရန် ဗီဒီယို တင်ပါ (English Audio)", 
                interactive=True
            )
            
            with gr.Accordion("⚙️ Voice & Video Settings", open=True):
                voice_select = gr.Dropdown(
                    choices=["မိန်းကလေးသံ (Nilar)", "ယောကျာ်းလေးသံ (Thiha)"],
                    value="မိန်းကလေးသံ (Nilar)",
                    label="🗣️ AI အသံ ရွေးချယ်ရန်"
                )
                speed_slider = gr.Slider(
                    minimum=0.5,
                    maximum=2.0,
                    value=1.0,
                    step=0.1,
                    label="⚡ ဗီဒီယိုနှင့် အသံ၏ အရှိန် (Speed)"
                )
                blur_y_slider = gr.Slider(
                    minimum=0, 
                    maximum=100, 
                    value=75, 
                    label="📍 မူရင်းစာတန်းဖျောက်မည့်နေရာ (Height %)"
                )
                
            submit_btn = gr.Button(
                "🚀 Perfect Sync Voiceover ပြုလုပ်မည်", 
                variant="primary",
                elem_classes="generate-btn"
            )
            
        with gr.Column(scale=1):
            video_output = gr.Video(label="✨ ထွက်လာသည့် ရလဒ် ဗီဒီယို (AI Voice Only)")
            
    submit_btn.click(
        fn=process_tiktok_video,
        inputs=[video_input, voice_select, speed_slider, blur_y_slider],
        outputs=video_output
    )

if __name__ == "__main__":
    if check_app_validity():
        demo.launch(auth=lambda u, p: USER_CREDENTIALS.get(u) == p, auth_message="Please enter Username and Password")