"""
Advanced Personal ClassPlus Bot - Complete Code
For GitHub + Render Deployment
"""

import os
import json
import sqlite3
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import requests
import jwt
from cryptography.fernet import Fernet

# Telegram imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes
)

# Flask for keep-alive server
from flask import Flask, jsonify
from threading import Thread

# ============ LOGGING SETUP ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(name)

# ============ CONFIGURATION ============
class Config:
    # Get from environment variables (Render)
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    OWNER_ID = int(os.environ.get('OWNER_ID', '0'))
    
    # Database
    DATABASE_PATH = 'classplus_bot.db'
    DOWNLOAD_DIR = 'downloads'
    
    # Token Configuration
    TOKEN_EXPIRY_DAYS = 30
    JWT_SECRET = os.urandom(32) if not os.environ.get('JWT_SECRET') else os.environ.get('JWT_SECRET').encode()
    ENCRYPTION_KEY = Fernet.generate_key()
    
    # Classplus API
    BASE_URL = "https://api.classplusapp.com/v2"
    
    # States
    (STATE_ORG_CODE, STATE_MOBILE, STATE_OTP, 
     STATE_TOKEN_LOGIN, STATE_PERMISSION_REASON) = range(5)
    
    # Flask server port
    PORT = int(os.environ.get('PORT', 10000))

# Create Flask app for keep-alive
flask_app = Flask(name)

@flask_app.route('/')
def home():
    return jsonify({
        'status': 'online',
        'bot': 'ClassPlus Bot',
        'version': '2.0',
        'timestamp': datetime.now().isoformat()
    })

@flask_app.route('/health')
def health():
    return jsonify({'status': 'healthy'}), 200

def run_web_server():
    """Run Flask server to keep bot alive"""
    flask_app.run(host='0.0.0.0', port=Config.PORT, debug=False)

# ============ DATABASE CLASS ============
class Database:
    def init(self, db_path: str):
        self.db_path = db_path
        self.cipher = Fernet(Config.ENCRYPTION_KEY)
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                encrypted_token TEXT,
                classplus_id INTEGER,
                org_code TEXT,
                mobile TEXT,
                token_created_at TIMESTAMP,
                token_expires_at TIMESTAMP,
                token_status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Allowed users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS allowed_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                approved_by INTEGER,
                can_extract BOOLEAN DEFAULT 1,
                can_upload BOOLEAN DEFAULT 1,
                approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Permission requests table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS permission_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                reason TEXT,
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending'
      )
        ''')
        
        # Downloads history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                course_name TEXT,
                item_name TEXT,
                item_type TEXT,
                status TEXT,
                downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Ensure owner is always allowed
        if Config.OWNER_ID:
            cursor.execute('''
                INSERT OR IGNORE INTO allowed_users (user_id, username, full_name, approved_by)
                VALUES (?, 'owner', 'Bot Owner', ?)
            ''', (Config.OWNER_ID, Config.OWNER_ID))
        
        conn.commit()
        conn.close()
        logger.info("Database initialized")
    
    def encrypt_token(self, token: str) -> str:
        return self.cipher.encrypt(token.encode()).decode()
    
    def decrypt_token(self, encrypted_token: str) -> str:
        return self.cipher.decrypt(encrypted_token.encode()).decode()
    
    def save_user(self, user_id: int, username: str, full_name: str, 
                  token: str, classplus_id: int, org_code: str, mobile: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        encrypted = self.encrypt_token(token)
        expires_at = datetime.now() + timedelta(days=Config.TOKEN_EXPIRY_DAYS)
        
        cursor.execute('''
            INSERT OR REPLACE INTO users 
            (user_id, username, full_name, encrypted_token, classplus_id, 
             org_code, mobile, token_created_at, token_expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, username, full_name, encrypted, classplus_id, 
              org_code, mobile, datetime.now(), expires_at))
        
        conn.commit()
        conn.close()
        logger.info(f"User {user_id} saved")
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            user_dict = dict(row)
            user_dict['token'] = self.decrypt_token(user_dict['encrypted_token'])
            return user_dict
        return None
    
    def is_allowed(self, user_id: int) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM allowed_users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def add_permission_request(self, user_id: int, username: str, reason: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO permission_requests (user_id, username, reason)
            VALUES (?, ?, ?)
        ''', (user_id, username, reason))
        conn.commit()
        conn.close()
    
    def get_pending_requests(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM permission_requests WHERE status = "pending"')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def approve_user(self, user_id: int, approved_by: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT username FROM permission_requests WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        username = row[0] if row else 'unknown'
      cursor.execute('''
            INSERT OR REPLACE INTO allowed_users (user_id, username, approved_by)
            VALUES (?, ?, ?)
        ''', (user_id, username, approved_by))
        
        cursor.execute('UPDATE permission_requests SET status = "approved" WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    
    def log_download(self, user_id: int, course_name: str, item_name: str, item_type: str, status: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO downloads (user_id, course_name, item_name, item_type, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, course_name, item_name, item_type, status))
        conn.commit()
        conn.close()

# ============ CLASSPLUS EXTRACTOR ============
class ClassplusExtractor:
    def init(self):
        self.session = requests.Session()
    
    def get_headers(self, token: str = ""):
        return {
            "Api-Version": "52",
            "Content-Type": "application/json",
            "Device-Id": f"web_{os.urandom(8).hex()}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "x-access-token": token
        }
    
    def login_with_token(self, token: str) -> tuple:
        try:
            headers = self.get_headers(token)
            response = self.session.get(f"{Config.BASE_URL}/users/profile", headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                user_data = data.get('data', {})
                return True, user_data.get('id'), user_data.get('mobile'), "✅ Token valid!"
            return False, None, None, "❌ Invalid token"
        except Exception as e:
            return False, None, None, f"❌ Error: {str(e)}"
    
    def send_otp(self, mobile: str, org_code: str) -> tuple:
        try:
            url = f"{Config.BASE_URL}/orgs/{org_code}"
            r = self.session.get(url, headers=self.get_headers())
            
            if r.status_code != 200:
                return None, None, "❌ Invalid Organization Code"
            
            org_data = r.json().get('data', {})
            org_id = org_data.get('orgId')
            
            payload = {
                'countryExt': '91',
                'mobile': mobile,
                'orgCode': org_code,
                'orgId': org_id,
                'viaSms': 1,
            }
            
            r = self.session.post(f"{Config.BASE_URL}/otp/generate", json=payload, headers=self.get_headers())
            
            if r.status_code == 200:
                session_id = r.json().get('data', {}).get('sessionId')
                return session_id, org_id, "✅ OTP Sent!"
            return None, None, "❌ Failed to send OTP"
        except Exception as e:
            return None, None, f"❌ Error: {str(e)[:50]}"
    
    def verify_otp(self, mobile: str, org_code: str, session_id: str, org_id: str, otp: str) -> tuple:
        try:
            payload = {
                "otp": otp,
                "countryExt": "91",
                "sessionId": session_id,
                "orgId": org_id,
                "mobile": mobile,
                "fingerprintId": os.urandom(16).hex()
            }
            
            r = self.session.post(f"{Config.BASE_URL}/users/verify", json=payload, headers=self.get_headers())
            
            if r.status_code == 200:
                res = r.json()
                if res.get('status') == 'success':
                    token = res['data']['token']
                    user_id = res['data']['user']['id']
                    return token, user_id, "✅ Login Successful!"
                return None, None, f"❌ {res.get('message', 'Verification failed')}"
              return None, None, "❌ Invalid OTP"
        except Exception as e:
            return None, None, f"❌ Error: {str(e)[:50]}"
    
    def get_courses(self, token: str, user_id: int) -> list:
        try:
            headers = self.get_headers(token)
            params = {'userId': user_id, 'tabCategoryId': 3}
            r = self.session.get(f'{Config.BASE_URL}/profiles/users/data', params=params, headers=headers)
            
            if r.status_code == 200:
                data = r.json().get('data', {})
                response_data = data.get('responseData', {})
                return response_data.get('coursesData', [])
        except Exception as e:
            logger.error(f"Get courses error: {e}")
        return []
    
    def get_course_content(self, token: str, course_id: int, folder_id: int = 0) -> list:
        contents = []
        headers = self.get_headers(token)
        params = {'courseId': course_id, 'folderId': folder_id}
        
        try:
            r = self.session.get(f'{Config.BASE_URL}/course/content/get', params=params, headers=headers)
            if r.status_code == 200:
                items = r.json().get('data', {}).get('courseContent', [])
                
                for item in items:
                    c_type = item.get('contentType')
                    name = item.get('name', 'Unnamed')
                    
                    if c_type == 1:
                        contents.extend(self.get_course_content(token, course_id, int(item.get('id', 0))))
                    elif c_type == 2:
                        contents.append({
                            'name': name,
                            'type': 'video',
                            'id': str(item.get('id'))
                        })
                    elif c_type == 3:
                        contents.append({
                            'name': name,
                            'type': 'pdf',
                            'url': item.get('url', ''),
                            'id': str(item.get('id'))
                        })
        except Exception as e:
            logger.error(f"Content error: {e}")
        
        return contents
    
    def get_download_url(self, token: str, content_id: str) -> Optional[str]:
        headers = self.get_headers(token)
        try:
            url = f'https://api.classplusapp.com/cams/uploader/video/jw-signed-url?contentId={content_id}'
            r = self.session.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                return data.get('data', {}).get('url') or data.get('url')
        except:
            pass
        return None

# ============ MAIN BOT ============
class AdvancedBot:
    def init(self):
        self.db = Database(Config.DATABASE_PATH)
        self.extractor = ClassplusExtractor()
        self.application = None
    
    def sanitize_filename(self, filename: str) -> str:
        import re
        return re.sub(r'[\\/*?:"<>|]', '', filename)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
        
        if user_id == Config.OWNER_ID:
            await self.owner_panel(update, context)
        elif self.db.is_allowed(user_id):
            await self.user_panel(update, context)
        else:
            await update.message.reply_text(
                "🔒 Access Restricted\n\nPlease provide a reason for access:",
                parse_mode="Markdown"
            )
            return Config.STATE_PERMISSION_REASON
    
    async def owner_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pending = len(self.db.get_pending_requests())
      pending_text = f" ({pending})" if pending > 0 else ""
        
        keyboard = [
            [InlineKeyboardButton("🔐 Classplus Login", callback_data="owner_login")],
            [InlineKeyboardButton("📚 Extract & Download", callback_data="owner_extract")],
            [InlineKeyboardButton(f"👥 Permission Requests{pending_text}", callback_data="owner_requests")],
            [InlineKeyboardButton("📊 Statistics", callback_data="owner_stats")],
            [InlineKeyboardButton("👑 Allowed Users", callback_data="owner_users")]
        ]
        
        await update.message.reply_text(
            "👑 OWNER PANEL\n\nSelect an option:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    
    async def user_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_data = self.db.get_user(update.effective_user.id)
        
        keyboard = []
        if user_data:
            keyboard.append([InlineKeyboardButton("📚 My Courses", callback_data="user_courses")])
            keyboard.append([InlineKeyboardButton("🔄 Refresh Login", callback_data="user_login")])
        else:
            keyboard.append([InlineKeyboardButton("🔐 Login to Classplus", callback_data="user_login")])
        
        keyboard.append([InlineKeyboardButton("📋 My Downloads", callback_data="user_history")])
        
        await update.message.reply_text(
            "🎓 User Panel\n\nSelect an option:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        data = query.data
        
        # Owner handlers
        if user_id == Config.OWNER_ID:
            if data == "owner_login":
                await query.edit_message_text("🔐 Enter organization code:")
                context.user_data['state'] = Config.STATE_ORG_CODE
                return Config.STATE_ORG_CODE
            
            elif data == "owner_extract":
                user_data = self.db.get_user(Config.OWNER_ID)
                if not user_data:
                    await query.edit_message_text("❌ Please login first!")
                    return
                
                courses = self.extractor.get_courses(user_data['token'], user_data['classplus_id'])
                if not courses:
                    await query.edit_message_text("❌ No courses found!")
                    return
                
                context.user_data['courses'] = courses
                keyboard = []
                for idx, course in enumerate(courses[:10]):
                    keyboard.append([InlineKeyboardButton(
                        f"📚 {course.get('name', 'Unknown')[:30]}",
                        callback_data=f"extract_{idx}_{course['id']}"
                    )])
                keyboard.append([InlineKeyboardButton("« Back", callback_data="back_owner")])
                
                await query.edit_message_text(
                    "📚 Select Course:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            
            elif data.startswith("extract_"):
                parts = data.split("_")
                course_id = int(parts[-1])
                user_data = self.db.get_user(Config.OWNER_ID)
                
                await query.edit_message_text("⏳ Extracting...")
                contents = self.extractor.get_course_content(user_data['token'], course_id)
                
                if not contents:
                    await query.edit_message_text("❌ No content found!")
                  return
                
                context.user_data['content'] = contents
                videos = sum(1 for c in contents if c['type'] == 'video')
                pdfs = sum(1 for c in contents if c['type'] == 'pdf')
                
                keyboard = [
                    [InlineKeyboardButton("⬇️ Download All", callback_data="download_all")],
                    [InlineKeyboardButton("📝 View List", callback_data="view_list")],
                    [InlineKeyboardButton("« Back", callback_data="owner_extract")]
                ]
                
                await query.edit_message_text(
                    f"✅ Extracted!\n\n🎬 Videos: {videos}\n📄 PDFs: {pdfs}\n📦 Total: {len(contents)}",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse
