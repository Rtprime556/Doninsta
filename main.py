import telebot
import instaloader
import os
import subprocess
import logging
from urllib.parse import urlparse
from flask import Flask
import threading
from keep_alive import keep_alive  # For additional uptime monitoring

# Initialize Flask app for health checks
app = Flask(__name__)

@app.route('/')
def health_check():
    """Endpoint for Koyeb health checks"""
    return "OK", 200

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN environment variable not set")

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Initialize bot and Instaloader
bot = telebot.TeleBot(BOT_TOKEN)
loader = instaloader.Instaloader(
    quiet=True,
    download_pictures=False,
    download_videos=True,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False
)

# Session management
user_sessions = {}

def extract_shortcode(url):
    """Extract Instagram shortcode from URL"""
    parsed = urlparse(url)
    if not parsed.netloc.endswith(('instagram.com', 'www.instagram.com')):
        raise ValueError("Invalid Instagram URL")
    
    path = parsed.path.strip('/')
    if '/reel/' in path:
        return path.split('/reel/')[1].split('/')[0]
    elif '/p/' in path:
        return path.split('/p/')[1].split('/')[0]
    elif len(path.split('/')) == 1 and path:
        return path
    raise ValueError("Couldn't extract reel shortcode")

def cleanup_directory():
    """Clean up download directory"""
    for filename in os.listdir(DOWNLOAD_DIR):
        file_path = os.path.join(DOWNLOAD_DIR, filename)
        try:
            if os.path.isfile(file_path):
                os.unlink(file_path)
        except Exception as e:
            logger.error(f"Failed to delete {file_path}: {e}")

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Welcome message handler"""
    welcome_msg = (
        "🌟 *Instagram Reels Downloader Bot* 🌟\n\n"
        "📤 *How to use:*\n"
        "1. Send me an Instagram Reel link\n"
        "2. Choose format (MP4 or MP3)\n"
        "3. Get your file!\n\n"
        "⚠ *Notes:*\n"
        "- Works with public reels only\n"
        "- Files are deleted after sending\n"
        "- Max duration: 90 seconds"
    )
    bot.send_message(message.chat.id, welcome_msg, parse_mode="Markdown")

@bot.message_handler(func=lambda m: any(
    x in m.text.lower() for x in ['instagram.com/reel/', 'instagram.com/p/']
))
def handle_reel_url(message):
    """Handle Instagram reel URLs"""
    try:
        clean_url = message.text.split('?')[0].strip()
        shortcode = extract_shortcode(clean_url)
        
        user_sessions[message.chat.id] = {
            'url': clean_url,
            'shortcode': shortcode
        }
        
        markup = telebot.types.ReplyKeyboardMarkup(
            one_time_keyboard=True,
            resize_keyboard=True
        )
        markup.add("MP4 🎥", "MP3 🎵")
        bot.send_message(
            message.chat.id,
            "Choose download format:",
            reply_markup=markup
        )
    except Exception as e:
        logger.error(f"URL handling error: {e}")
        bot.reply_to(
            message,
            "❌ Invalid Reel URL. Please send a valid public Instagram Reel link."
        )

@bot.message_handler(func=lambda m: m.text in ["MP4 🎥", "MP3 🎵"])
def process_download(message):
    """Process download requests"""
    chat_id = message.chat.id
    if chat_id not in user_sessions:
        return bot.reply_to(message, "⚠ Please send a Reel link first")
    
    try:
        bot.send_chat_action(chat_id, 'typing')
        shortcode = user_sessions[chat_id]['shortcode']
        format_type = "video" if "MP4" in message.text else "audio"
        
        # Download the reel
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
        loader.download_post(post, target=DOWNLOAD_DIR)
        
        # Find downloaded file
        video_file = next(
            (f for f in os.listdir(DOWNLOAD_DIR) 
            if f.endswith('.mp4') and shortcode in f
        )
        if not video_file:
            raise FileNotFoundError("Downloaded file not found")
        
        file_path = os.path.join(DOWNLOAD_DIR, video_file)
        
        if format_type == "video":
            bot.send_chat_action(chat_id, 'upload_video')
            with open(file_path, 'rb') as f:
                bot.send_video(
                    chat_id,
                    f,
                    caption="Here's your Instagram Reel 📹",
                    supports_streaming=True
                )
        else:
            # Convert to MP3
            audio_path = os.path.join(DOWNLOAD_DIR, f"{shortcode}.mp3")
            subprocess.run(
                ['ffmpeg', '-i', file_path, '-q:a', '0', '-map', 'a', audio_path],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            bot.send_chat_action(chat_id, 'upload_audio')
            with open(audio_path, 'rb') as f:
                bot.send_audio(
                    chat_id,
                    f,
                    title="Instagram Reel Audio"
                )
            os.remove(audio_path)
        
        # Cleanup
        os.remove(file_path)
        user_sessions.pop(chat_id, None)
        
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("📥 Download Another")
        bot.send_message(
            chat_id,
            "✅ Download complete!",
            reply_markup=markup
        )
        
    except instaloader.exceptions.BadResponseException:
        bot.reply_to(
            message,
            "❌ Instagram error. The reel might be private or unavailable."
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg error: {e.stderr.decode()}")
        bot.reply_to(message, "❌ Audio conversion failed")
    except Exception as e:
        logger.error(f"Download error: {e}")
        bot.reply_to(message, f"❌ Error: {str(e)}")
    finally:
        cleanup_directory()

@bot.message_handler(func=lambda m: m.text == "📥 Download Another")
def restart_flow(message):
    """Restart the download process"""
    bot.send_message(
        message.chat.id,
        "Send me another Instagram Reel link:",
        reply_markup=telebot.types.ReplyKeyboardRemove()
    )

def run_flask():
    """Run Flask web server for health checks"""
    app.run(host='0.0.0.0', port=8080)

if __name__ == "__main__":
    # Start Flask server in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Start uptime monitoring (optional)
    keep_alive()
    
    logger.info("Starting Instagram Reels Downloader Bot")
    bot.infinity_polling()