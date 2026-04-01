import sqlite3
import re
import csv
import os
import threading
import asyncio
import psycopg2
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ========== 設定香港時區 ==========
HONG_KONG_TZ = timezone(timedelta(hours=8))

def get_hk_time():
    """獲取香港時間"""
    return datetime.now(HONG_KONG_TZ)

def get_hk_time_str():
    """獲取香港時間字串"""
    return get_hk_time().strftime("%Y-%m-%d %H:%M:%S")

def get_hk_date():
    """獲取香港日期"""
    return get_hk_time().strftime("%Y-%m-%d")

# ========== 配置 ==========
TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TOKEN_HERE")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DEFAULT_FEE_RATE = 3.5
DEFAULT_EXCHANGE_RATE = 7.9

ADMIN_USER_IDS = []

# ========== 輔助函數：將群組ID轉換為安全表名 ==========
def safe_table_name(chat_id):
    """將群組ID轉換為安全的表名（將 - 替換為 _）"""
    return str(chat_id).replace('-', '_')

# ========== 資料庫連接 ==========
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# ========== 初始化群組表 ==========
def init_groups_table():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            id BIGINT PRIMARY KEY,
            name TEXT,
            added_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

def add_group(chat_id, chat_name):
    """記錄群組"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO groups (id, name, added_at) 
        VALUES (%s, %s, %s) 
        ON CONFLICT (id) DO NOTHING
    ''', (chat_id, chat_name, get_hk_time_str()))
    conn.commit()
    conn.close()

def get_all_groups():
    """獲取所有群組 ID"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM groups")
    groups = [row[0] for row in c.fetchall()]
    conn.close()
    return groups

# ========== 為每個群組創建獨立表 ==========
def init_group_table(chat_id):
    """為群組創建獨立的交易記錄表"""
    conn = get_db_connection()
    c = conn.cursor()
    safe_id = safe_table_name(chat_id)
    table_name = f"transactions_{safe_id}"
    c.execute(f'''
        CREATE TABLE IF NOT EXISTS {table_name} (
            id SERIAL PRIMARY KEY,
            type TEXT,
            amount_hkd REAL,
            amount_usdt REAL,
            actual_hkd REAL,
            customer TEXT,
            operator TEXT,
            date TEXT
        )
    ''')
    
    # 同時創建設定表（每個群組獨立費率匯率）
    settings_table = f"settings_{safe_id}"
    c.execute(f'''
        CREATE TABLE IF NOT EXISTS {settings_table} (
            id SERIAL PRIMARY KEY,
            fee_rate REAL,
            exchange_rate REAL,
            updated_at TEXT
        )
    ''')
    
    # 檢查是否有設定，如果沒有就插入默認值
    c.execute(f"SELECT COUNT(*) FROM {settings_table}")
    if c.fetchone()[0] == 0:
        c.execute(f"INSERT INTO {settings_table} (fee_rate, exchange_rate, updated_at) VALUES (%s, %s, %s)",
                  (DEFAULT_FEE_RATE, DEFAULT_EXCHANGE_RATE, get_hk_time_str()))
    
    conn.commit()
    conn.close()

# ========== 獲取群組的費率匯率 ==========
def get_group_rates(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    safe_id = safe_table_name(chat_id)
    table_name = f"settings_{safe_id}"
    try:
        c.execute(f"SELECT fee_rate, exchange_rate FROM {table_name} ORDER BY id DESC LIMIT 1")
        result = c.fetchone()
        conn.close()
        if result:
            return result[0], result[1]
    except:
        pass
    conn.close()
    return DEFAULT_FEE_RATE, DEFAULT_EXCHANGE_RATE

def update_group_rates(chat_id, fee_rate=None, exchange_rate=None):
    conn = get_db_connection()
    c = conn.cursor()
    current_fee, current_exchange = get_group_rates(chat_id)
    new_fee = fee_rate if fee_rate is not None else current_fee
    new_exchange = exchange_rate if exchange_rate is not None else current_exchange
    safe_id = safe_table_name(chat_id)
    table_name = f"settings_{safe_id}"
    c.execute(f"INSERT INTO {table_name} (fee_rate, exchange_rate, updated_at) VALUES (%s, %s, %s)",
              (new_fee, new_exchange, get_hk_time_str()))
    conn.commit()
    conn.close()
    return new_fee, new_exchange

# ========== 計算函數 ==========
def calculate_income(amount_hkd, fee_rate, exchange_rate):
    """入款：港幣 × (1 - 費率%) ÷ 匯率 = USDT"""
    return amount_hkd * (1 - fee_rate / 100) / exchange_rate

def calculate_actual_hkd(amount_hkd, fee_rate):
    """扣除費率後的實際港幣（應下發金額）"""
    return amount_hkd * (1 - fee_rate / 100)

def calculate_expense(amount_hkd, exchange_rate):
    """下發：港幣 ÷ 匯率 = USDT"""
    return amount_hkd / exchange_rate

# ========== 交易操作（群組獨立）==========
def add_transaction_group(chat_id, type, amount_hkd, amount_usdt, actual_hkd, customer, operator):
    conn = get_db_connection()
    c = conn.cursor()
    safe_id = safe_table_name(chat_id)
    table_name = f"transactions_{safe_id}"
    c.execute(f"INSERT INTO {table_name} (type, amount_hkd, amount_usdt, actual_hkd, customer, operator, date) VALUES (%s, %s, %s, %s, %s, %s, %s)",
              (type, amount_hkd, amount_usdt, actual_hkd, customer, operator, get_hk_time_str()))
    conn.commit()
    conn.close()

def get_today_transactions_group(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    today = get_hk_date()
    safe_id = safe_table_name(chat_id)
    table_name = f"transactions_{safe_id}"
    try:
        c.execute(f"SELECT type, amount_hkd, amount_usdt, actual_hkd, customer, operator, date FROM {table_name} WHERE date LIKE %s ORDER BY date ASC", (f"{today}%",))
        results = c.fetchall()
    except:
        results = []
    conn.close()
    return results

def get_today_stats_group(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    today = get_hk_date()
    safe_id = safe_table_name(chat_id)
    table_name = f"transactions_{safe_id}"
    try:
        # 入款總額（原始港幣）
        c.execute(f"SELECT SUM(amount_hkd) FROM {table_name} WHERE type = 'income' AND date LIKE %s", (f"{today}%",))
        income_original = c.fetchone()[0] or 0
        # 入款實際應下發總額（扣費後）
        c.execute(f"SELECT SUM(actual_hkd) FROM {table_name} WHERE type = 'income' AND date LIKE %s", (f"{today}%",))
        income_actual = c.fetchone()[0] or 0
        # 入款 USDT 總額
        c.execute(f"SELECT SUM(amount_usdt) FROM {table_name} WHERE type = 'income' AND date LIKE %s", (f"{today}%",))
        income_usdt = c.fetchone()[0] or 0
        # 下發總額
        c.execute(f"SELECT SUM(amount_hkd) FROM {table_name} WHERE type = 'expense' AND date LIKE %s", (f"{today}%",))
        expense_hkd = c.fetchone()[0] or 0
        c.execute(f"SELECT SUM(amount_usdt) FROM {table_name} WHERE type = 'expense' AND date LIKE %s", (f"{today}%",))
        expense_usdt = c.fetchone()[0] or 0
    except:
        income_original, income_actual, income_usdt, expense_hkd, expense_usdt = 0, 0, 0, 0, 0
    conn.close()
    return {
        'income_original': income_original,
        'income_actual': income_actual,
        'income_usdt': income_usdt,
        'expense_hkd': expense_hkd,
        'expense_usdt': expense_usdt
    }

def get_all_stats_group(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    safe_id = safe_table_name(chat_id)
    table_name = f"transactions_{safe_id}"
    try:
        # 所有入款實際應下發總額（扣費後）
        c.execute(f"SELECT SUM(actual_hkd) FROM {table_name} WHERE type = 'income'")
        total_income_actual = c.fetchone()[0] or 0
        # 所有下發總額
        c.execute(f"SELECT SUM(amount_hkd) FROM {table_name} WHERE type = 'expense'")
        total_expense = c.fetchone()[0] or 0
    except:
        total_income_actual, total_expense = 0, 0
    return total_income_actual, total_expense

def get_total_income_original_group(chat_id):
    """獲取原始入款總額（不扣費）"""
    conn = get_db_connection()
    c = conn.cursor()
    safe_id = safe_table_name(chat_id)
    table_name = f"transactions_{safe_id}"
    try:
        c.execute(f"SELECT SUM(amount_hkd) FROM {table_name} WHERE type = 'income'")
        total = c.fetchone()[0] or 0
    except:
        total = 0
    conn.close()
    return total

def get_last_transaction_group(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    safe_id = safe_table_name(chat_id)
    table_name = f"transactions_{safe_id}"
    try:
        c.execute(f"SELECT id, type, amount_hkd, customer FROM {table_name} ORDER BY date DESC LIMIT 1")
        result = c.fetchone()
    except:
        result = None
    conn.close()
    return result

def cancel_transaction_group(chat_id, transaction_id):
    conn = get_db_connection()
    c = conn.cursor()
    safe_id = safe_table_name(chat_id)
    table_name = f"transactions_{safe_id}"
    c.execute(f"DELETE FROM {table_name} WHERE id = %s", (transaction_id,))
    conn.commit()
    affected = c.rowcount
    conn.close()
    return affected > 0

def export_to_csv_group(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    safe_id = safe_table_name(chat_id)
    table_name = f"transactions_{safe_id}"
    try:
        c.execute(f"SELECT id, type, amount_hkd, amount_usdt, actual_hkd, customer, operator, date FROM {table_name} ORDER BY date DESC")
        transactions = c.fetchall()
    except:
        transactions = []
    conn.close()
    
    if not transactions:
        return None
    
    fee_rate, exchange_rate = get_group_rates(chat_id)
    daily_data = {}
    for id, type, hkd, usdt, actual_hkd, customer, operator, date in transactions:
        date_str = date.split()[0]
        if date_str not in daily_data:
            daily_data[date_str] = {'income_original': 0, 'income_actual': 0, 'expense': 0}
        if type == 'income':
            daily_data[date_str]['income_original'] += hkd
            daily_data[date_str]['income_actual'] += actual_hkd
        else:
            daily_data[date_str]['expense'] += hkd
    
    sorted_dates = sorted(daily_data.keys())
    filename = f"daily_report_{safe_id}_{get_hk_time().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = os.path.join(os.path.dirname(__file__), filename)
    
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['每日財務明細報表'])
        writer.writerow(['生成時間', get_hk_time_str()])
        writer.writerow(['費率', f"{fee_rate}%"])
        writer.writerow(['匯率', str(exchange_rate)])
        writer.writerow([])
        writer.writerow(['日期', '入款金額 (HKD)', f'費率扣除 ({fee_rate}%)', '實際入款 (HKD)', '下發金額 (HKD)', '當日結餘 (HKD)', '累積結餘 (HKD)'])
        
        cumulative_balance = 0
        for date_str in sorted_dates:
            income_original = daily_data[date_str]['income_original']
            income_actual = daily_data[date_str]['income_actual']
            expense = daily_data[date_str]['expense']
            fee_deduction = income_original - income_actual
            daily_balance = income_actual - expense
            cumulative_balance += daily_balance
            writer.writerow([date_str, f"{income_original:,.2f}", f"{fee_deduction:,.2f}", f"{income_actual:,.2f}", f"{expense:,.2f}", f"{daily_balance:,.2f}", f"{cumulative_balance:,.2f}"])
        
        writer.writerow([])
        writer.writerow(['=== 總結 ==='])
        writer.writerow(['總入款 (HKD)', f"{sum(d['income_original'] for d in daily_data.values()):,.2f}"])
        writer.writerow(['總實際入款 (HKD)', f"{sum(d['income_actual'] for d in daily_data.values()):,.2f}"])
        writer.writerow(['總下發 (HKD)', f"{sum(d['expense'] for d in daily_data.values()):,.2f}"])
        writer.writerow(['最終累積結餘 (HKD)', f"{cumulative_balance:,.2f}"])
        writer.writerow(['最終累積結餘 (USDT)', f"{cumulative_balance / exchange_rate:.2f}"])
    
    return filepath

# ========== Telegram 命令 ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    chat_id = chat.id
    
    if chat.type in ['group', 'supergroup']:
        add_group(chat_id, chat.title)
        init_group_table(chat_id)
        await update.message.reply_text(f"✅ 本群組已初始化獨立記帳系統！\n\n每晚 11:59 會自動發送今日明細到本群組")
    
    fee_rate, exchange_rate = get_group_rates(chat_id)
    text = f"""當前設定:
📊 費率: {fee_rate}%
💱 匯率: {exchange_rate} HKD/USDT

記帳方式 (只能引用):

1️⃣ 入款: 引用客戶訊息，輸入 +金額
2️⃣ 下發: 引用客戶訊息，輸入 -金額

查看報表:
/list - 今日明細
/stats - 今日統計
/export - 匯出 Excel 報表
/undo - 撤銷最後一筆

管理設定:
/fee 3.5 - 設置費率
/rate 7.9 - 設置匯率

定時報表:
每晚 11:59 自動發送今日明細到本群組"""
    await update.message.reply_text(text)

async def set_fee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not await is_admin(update):
        await update.message.reply_text("❌ 只有群組管理員才能使用此功能")
        return
    if not context.args:
        await update.message.reply_text("請輸入費率，例如: /fee 3.5")
        return
    try:
        new_fee = float(context.args[0])
        update_group_rates(chat_id, fee_rate=new_fee)
        await update.message.reply_text(f"✅ 費率已更新為: {new_fee}%")
    except:
        await update.message.reply_text("❌ 請輸入有效的數字")

async def set_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not await is_admin(update):
        await update.message.reply_text("❌ 只有群組管理員才能使用此功能")
        return
    if not context.args:
        await update.message.reply_text("請輸入匯率，例如: /rate 7.9")
        return
    try:
        new_rate = float(context.args[0])
        update_group_rates(chat_id, exchange_rate=new_rate)
        await update.message.reply_text(f"✅ 匯率已更新為: {new_rate}")
    except:
        await update.message.reply_text("❌ 請輸入有效的數字")

async def is_admin(update: Update) -> bool:
    try:
        user_id = update.effective_user.id
        if user_id in ADMIN_USER_IDS:
            return True
        if update.effective_chat.type == 'private':
            return True
        if update.effective_chat.type in ['group', 'supergroup']:
            try:
                bot = update.get_bot()
                chat_member = await bot.get_chat_member(update.effective_chat.id, user_id)
                if chat_member.status in ['administrator', 'creator']:
                    return True
            except:
                return False
        return False
    except:
        return False

async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """只顯示今日明細（入款/下發逐筆記錄）"""
    chat_id = update.effective_chat.id
    transactions = get_today_transactions_group(chat_id)
    fee_rate, exchange_rate = get_group_rates(chat_id)
    
    if not transactions:
        await update.message.reply_text("📭 今日暫無交易記錄")
        return
    
    incomes = []
    expenses = []
    for type, hkd, usdt, actual_hkd, customer, operator, date in transactions:
        time = date.split()[1][:5] if date else ""
        if type == 'income':
            incomes.append((time, hkd, usdt, customer, operator))
        else:
            expenses.append((time, hkd, usdt, customer, operator))
    
    text = ""
    if incomes:
        text += f"今日入款（{len(incomes)}筆）\n"
        for time, hkd, usdt, customer, operator in incomes:
            text += f"{time}  {hkd:.0f}*{1 - fee_rate/100:.3f} / {exchange_rate}={usdt:.2f}U   {customer}  {operator}\n"
        text += "\n"
    if expenses:
        text += f"今日下發（{len(expenses)}筆）\n"
        for time, hkd, usdt, customer, operator in expenses:
            text += f"{time}  {hkd:.0f} / {exchange_rate}={usdt:.2f}U   {customer}  {operator}\n"
    
    await update.message.reply_text(text)

async def show_stats_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """只顯示統計（記帳後用）- 已扣除費率"""
    chat_id = update.effective_chat.id
    stats = get_today_stats_group(chat_id)
    fee_rate, exchange_rate = get_group_rates(chat_id)
    total_income_actual, total_expense = get_all_stats_group(chat_id)
    total_income_original = get_total_income_original_group(chat_id)
    
    cumulative_balance = total_income_actual - total_expense
    cumulative_balance_u = cumulative_balance / exchange_rate
    
    text = f"""📊 今日統計
今日入款: {stats['income_original']:,.1f} HKD
今日實際入款 (扣費後): {stats['income_actual']:,.1f} HKD
今日下發: {stats['expense_hkd']:,.1f} HKD

📈 累積結餘
總入款金額: {total_income_original:,.1f} HKD
總下發金額: {total_expense:,.1f} HKD
費率: {fee_rate}%
固定匯率: {exchange_rate}

累計應下發: {total_income_actual:,.1f} | {total_income_actual / exchange_rate:.2f} u
累計已下發: {total_expense:.0f} | {total_expense / exchange_rate:.2f} u
累計未下發: {cumulative_balance:,.1f} | {cumulative_balance_u:.2f} u"""
    await update.message.reply_text(text)

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """今日統計（保留兼容）"""
    await show_stats_only(update, context)

async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("📊 正在生成報表，請稍候...")
    filepath = export_to_csv_group(chat_id)
    if not filepath:
        await update.message.reply_text("📭 沒有任何交易記錄可匯出")
        return
    try:
        with open(filepath, 'rb') as f:
            await update.message.reply_document(document=f, filename=os.path.basename(filepath), caption=f"📊 每日財務明細報表\n生成時間: {get_hk_time_str()}")
        os.remove(filepath)
    except Exception as e:
        await update.message.reply_text(f"❌ 匯出失敗: {e}")

async def cancel_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not await is_admin(update):
        await update.message.reply_text("❌ 只有群組管理員才能使用此功能")
        return
    last = get_last_transaction_group(chat_id)
    if not last:
        await update.message.reply_text("❌ 沒有找到可撤銷的交易")
        return
    tid, ttype, amount, customer = last
    type_text = "入款" if ttype == 'income' else "下發"
    if cancel_transaction_group(chat_id, tid):
        await update.message.reply_text(f"✅ 已撤銷最後一筆{type_text}: {amount:,.2f} HKD (客戶: {customer})")
    else:
        await update.message.reply_text("❌ 撤銷失敗")

async def handle_quick_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理記帳 - 支援文字訊息和帶說明的照片"""
    
    # 獲取訊息文字（不論是文字訊息還是照片說明）
    text = None
    message_type = "unknown"
    
    if update.message.text:
        text = update.message.text.strip()
        message_type = "text"
    elif update.message.caption:
        text = update.message.caption.strip()
        message_type = "photo_caption"
    
    # 調試
    print(f"訊息類型: {message_type}, 內容: {text}")
    
    # 如果不是以 + 或 - 開頭，直接忽略
    if not text or (not text.startswith('+') and not text.startswith('-')):
        return
    
    chat_id = update.effective_chat.id
    
    # 檢查權限
    if not await is_admin(update):
        await update.message.reply_text("❌ 只有群組管理員才能使用記帳功能")
        return
    
    fee_rate, exchange_rate = get_group_rates(chat_id)
    operator = update.effective_user.first_name or update.effective_user.username or "管理員"
    
    # 獲取客戶名稱：從被引用的訊息獲取
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ 請引用客戶的訊息來記帳\n\n例如：引用客戶的訊息後輸入 +5000 或 -3000")
        return
    
    replied_user = update.message.reply_to_message.from_user
    customer = replied_user.first_name or replied_user.username or "未知客戶"
    
    print(f"客戶: {customer}, 操作人: {operator}, 金額: {text}")
    
    # 處理入款
    match = re.match(r'^\+(\d+(?:\.\d+)?)$', text)
    if match:
        amount_hkd = float(match.group(1))
        amount_usdt = calculate_income(amount_hkd, fee_rate, exchange_rate)
        actual_hkd = calculate_actual_hkd(amount_hkd, fee_rate)
        add_transaction_group(chat_id, 'income', amount_hkd, amount_usdt, actual_hkd, customer, operator)
        await show_stats_only(update, context)
        return
    
    # 處理下發
    match = re.match(r'^-(\d+(?:\.\d+)?)$', text)
    if match:
        amount_hkd = float(match.group(1))
        amount_usdt = calculate_expense(amount_hkd, exchange_rate)
        add_transaction_group(chat_id, 'expense', amount_hkd, amount_usdt, amount_hkd, customer, operator)
        await show_stats_only(update, context)
        return
    
    # 格式錯誤
    await update.message.reply_text("❌ 格式錯誤\n\n正確格式：\n引用客戶訊息後輸入：\n+金額  → 入款\n-金額  → 下發\n\n例如：+5000 或 -3000")

# ========== 定時報表功能 ==========
async def send_daily_report(app):
    """每晚11:59自動發送今日明細到所有群組"""
    groups = get_all_groups()
    
    for chat_id in groups:
        transactions = get_today_transactions_group(chat_id)
        fee_rate, exchange_rate = get_group_rates(chat_id)
        
        if not transactions:
            report = "📭 今日無交易記錄"
        else:
            incomes = []
            expenses = []
            for type, hkd, usdt, actual_hkd, customer, operator, date in transactions:
                time = date.split()[1][:5] if date else ""
                if type == 'income':
                    incomes.append((time, hkd, usdt, customer, operator))
                else:
                    expenses.append((time, hkd, usdt, customer, operator))
            
            report = ""
            if incomes:
                report += f"📊 今日入款（{len(incomes)}筆）\n"
                for time, hkd, usdt, customer, operator in incomes:
                    report += f"{time}  {hkd:.0f}*{1 - fee_rate/100:.3f} / {exchange_rate}={usdt:.2f}U   {customer}  {operator}\n"
                report += "\n"
            if expenses:
                report += f"📊 今日下發（{len(expenses)}筆）\n"
                for time, hkd, usdt, customer, operator in expenses:
                    report += f"{time}  {hkd:.0f} / {exchange_rate}={usdt:.2f}U   {customer}  {operator}\n"
        
        try:
            await app.bot.send_message(chat_id=chat_id, text=report)
            print(f"✅ 定時報表已發送到群組 {chat_id}")
        except Exception as e:
            print(f"❌ 發送到群組 {chat_id} 失敗: {e}")

# ========== 主程式 ==========
def main():
    init_groups_table()
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("fee", set_fee))
    app.add_handler(CommandHandler("rate", set_exchange))
    app.add_handler(CommandHandler("stats", show_stats))
    app.add_handler(CommandHandler("list", show_list))
    app.add_handler(CommandHandler("export", export_excel))
    app.add_handler(CommandHandler("undo", cancel_last))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quick_input))
    
    # 使用 threading.Timer 實現定時報表
    def schedule_report():
        # 獲取當前香港時間
        now = get_hk_time()
        # 目標時間 23:59
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if now >= target:
            # 如果已經過了今天的 23:59，則設定到明天
            target += timedelta(days=1)
        
        # 計算等待秒數
        wait_seconds = (target - now).total_seconds()
        print(f"下次報表將在 {wait_seconds:.0f} 秒後發送（{target.strftime('%Y-%m-%d %H:%M:%S')}）")
        
        # 設定定時器
        def send_report_wrapper():
            asyncio.run_coroutine_threadsafe(send_daily_report(app), app.loop)
        
        timer = threading.Timer(wait_seconds, send_report_wrapper)
        timer.daemon = True
        timer.start()
        
        # 每天重新調度
        next_timer = threading.Timer(wait_seconds + 1, schedule_report)
        next_timer.daemon = True
        next_timer.start()
    
    # 啟動定時調度
    schedule_report()
    
    print("🤖 財務記帳機器人啟動中...")
    print("📝 記帳方式: 只能引用客戶訊息")
    print("🔐 權限設定: 只有群組管理員才能記帳")
    print("⏰ 定時報表已設定: 每晚 23:59 (香港時間) 自動發送")
    print("🏢 群組獨立記帳: 每個群組的記錄完全分開")
    print("💰 入款會自動扣除費率，下發不扣費率")
    
    app.run_polling()

if __name__ == "__main__":
    main()
