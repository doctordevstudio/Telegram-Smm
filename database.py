import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime
import config

# Initialize Firebase
if not firebase_admin._apps:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred, {'databaseURL': config.FIREBASE_URL})

# --- Users ---
def get_user(chat_id): return db.reference(f'users/{chat_id}').get()
def get_all_users(): return db.reference('users').get() or {}

def create_user(chat_id, full_name, username, profile_pic_id=None):
    db.reference(f'users/{chat_id}').set({
        'chat_id': chat_id,
        'full_name': full_name,
        'username': username or "NoUsername",
        'profile_pic_id': profile_pic_id,
        'wallet_balance': 0,
        'is_banned': False,
        'joined_at': datetime.now().isoformat()
    })

def update_wallet(chat_id, amount):
    ref = db.reference(f'users/{chat_id}/wallet_balance')
    current = ref.get() or 0
    ref.set(current + amount)
    return current + amount

def update_ban_status(chat_id, is_banned):
    db.reference(f'users/{chat_id}/is_banned').set(is_banned)

# --- Plans & Settings ---
def get_settings(): return db.reference('settings').get() or {}
def update_settings(key, value): db.reference(f'settings/{key}').set(value)

def get_service_plans(service_type):
    default = {
        "subscribers": {"unit_price": 0.5}, "views": {"unit_price": 0.1},
        "reactions": {"unit_price": 0.2}, "livestream": {"unit_price": 1.0}
    }
    return db.reference(f'plans/{service_type}').get() or default.get(service_type, {})

# --- Payments & Orders ---
def log_payment(pay_id, chat_id, amount, status, ss_id="", reason=""):
    db.reference(f'payments/{pay_id}').set({
        'user_id': chat_id, 'amount': amount, 'status': status,
        'ss_id': ss_id, 'reason': reason, 'timestamp': datetime.now().isoformat()
    })

def get_all_payments(): return db.reference('payments').get() or {}

def process_order_payment(chat_id, amount, order_data):
    current = db.reference(f'users/{chat_id}/wallet_balance').get() or 0
    if current < amount: return False
    db.reference(f'users/{chat_id}/wallet_balance').set(current - amount)
    
    order_id = str(datetime.now().timestamp()).replace(".", "")[-8:]
    order_data.update({'status': "processing", 'timestamp': datetime.now().isoformat()})
    db.reference(f'orders/{order_id}').set(order_data)
    return order_id

def update_order_status(order_id, status): db.reference(f'orders/{order_id}/status').set(status)
def get_user_history(chat_id):
    orders = db.reference('orders').order_by_child('user_id').equal_to(chat_id).get() or {}
    return sorted([{"id": k, **v} for k, v in orders.items()], key=lambda x: x['timestamp'], reverse=True)
def get_all_orders(): return db.reference('orders').get() or {}
