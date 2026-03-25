import sqlite3
import re
import csv
import os
import threading
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

# ========== 資料庫連接 ==========
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# ========== 資料庫初始化 ==========
def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # 創建交易記錄表
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            type TEXT,
            amount_hkd REAL,
            amount_usdt REAL,
            customer TEXT,
            operator TEXT,
            date TEXT
        )
    ''')
    
    # 創建設定表
    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            id SERIAL PRIMARY KEY,
            fee_rate REAL,
            exchange_rate REAL,
            updated_at TEXT
        )
    ''')
    
    # 檢查是否有設定，如果沒有就插入默認值
    c.execute("SELECT COUNT(*) FROM settings")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO settings (fee_rate, exchange_rate, updated_at) VALUES (%s, %s, %s)",
                  (DEFAULT_FEE_RATE, DEFAULT_EXCHANGE_RATE, get_hk_time_str()))
    
    conn.commit()
    conn.close()

def get_current_rates():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT fee_rate, exchange_rate FROM settings ORDER BY id DESC LIMIT 1")
    result = c.fetchone()
    conn.close()
    if result:
        return result[0], result[1]
    return DEFAULT_FEE_RATE, DEFAULT_EXCHANGE_RATE

def update_rates(fee_rate=None, exchange_rate=None):
    conn = get_db_connection()
    c = conn.cursor()
    current_fee, current_exchange = get_current_rates()
    new_fee = fee_rate if fee_rate is not None else current_fee
    new_exchange = exchange_rate if exchange_rate is not None else current_exchange
    c.execute("INSERT INTO settings (fee_rate, exchange_rate, updated_at) VALUES (%s, %s, %s)",
              (new_fee, new_exchange, get_hk_time_str()))
    conn.commit()
    conn.close()

def calculate_income(amount_hkd, fee_rate, exchange_rate):
    return amount_hkd * (1 - fee_rate / 100) / exchange_rate

def calculate_expense(amount_hkd, exchange_rate):
    return amount_hkd / exchange_rate

def add_transaction(type, amount_hkd, amount_usdt, customer, operator):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO transactions (type, amount_hkd, amount_usdt, customer, operator, date) VALUES (%s, %s, %s, %s, %s, %s)",
              (type, amount_hkd, amount_usdt, customer, operator, get_hk_time_str()))
    conn.commit()
    conn.close()

def get_today_transactions():
    conn = get_db_connection()
    c = conn.cursor()
    today = get_hk_date()
    c.execute("SELECT type, amount_hkd, amount_usdt, customer, operator, date FROM transactions WHERE date LIKE %s ORDER BY date ASC", (f"{today}%",))
    results = c.fetchall()
    conn.close()
    return results

def get_today_stats():
    conn = get_db_connection()
    c = conn.cursor()
    today = get_hk_date()
    c.execute("SELECT SUM(amount_hkd), SUM(amount_usdt) FROM transactions WHERE type = 'income' AND date LIKE %s", (f"{today}%",))
    income_hkd, income_usdt = c.fetchone() or (0, 0)
    c.execute("SELECT SUM(amount_hkd), SUM(amount_usdt) FROM transactions WHERE type = 'expense' AND date LIKE %s", (f"{today}%",))
    expense_hkd, expense_usdt = c.fetchone() or (0, 0)
    conn.close()
    income_hkd = income_hkd or 0
    income_usdt = income_usdt or 0
    expense_hkd = expense_hkd or 0
    expense_usdt = expense_usdt or 0
    return {
        'income_hkd': income_hkd,
        'income_usdt': income_usdt,
        'expense_hkd': expense_hkd,
        'expense_usdt': expense_usdt
    }

def get_all_stats():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT SUM(amount_hkd) FROM transactions WHERE type = 'income'")
    total_income_all = c.fetchone()[0] or 0
    c.execute("SELECT SUM(amount_hkd) FROM transactions WHERE type = 'expense'")
    total_expense_all = c.fetchone()[0] or 0
    conn.close()
    return total_income_all, total_expense_all

def get_all_transactions():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, type, amount_hkd, amount_usdt, customer, operator, date FROM transactions ORDER BY date DESC")
    results = c.fetchall()
    conn.close()
    return results

def get_last_transaction():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, type, amount_hkd, customer FROM transactions ORDER BY date DESC LIMIT 1")
    result = c.fetchone()
    conn.close()
    return result

def cancel_transaction(transaction_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM transactions WHERE id = %s", (transaction_id,))
    conn.commit()
    affected = c.rowcount
    conn.close()
    return affected > 0

def export_to_csv():
    transactions = get_all_transactions()
    if not transactions:
        return None
    
    fee_rate, exchange_rate = get_current_rates()
    daily_data = {}
    for type, hkd, usdt, customer, operator, date in transactions:
        date_str = date.split()[0]
        if date_str not in daily_data:
            daily_data[date_str] = {'income': 0, 'expense': 0}
        if type == 'income':
            daily_data[date_str]['income'] += hkd
        else:
            daily_data[date_str]['expense'] += hkd
    
    sorted_dates = sorted(daily_data.keys())
    filename = f"daily_report_{get_hk_time().strftime('%Y%m%d_%H%M%S')}.csv"
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
            income = daily_data[date_str]['income']
            expense = daily_data[date_str]['expense']
            fee_deduction = income * (fee_rate / 100)
            actual_income = income - fee_deduction
            daily_balance = actual_income - expense
            cumulative_balance += daily_balance
            writer.writerow([date_str, f"{income:,.2f}", f"{fee_deduction:,.2f}", f"{actual_income:,.2f}", f"{expense:,.2f}", f"{daily_balance:,.2f}", f"{cumulative_balance:,.2f}"])
        
        writer.writerow([])
        writer.writerow(['=== 總結 ==='])
        writer.writerow(['總入款 (HKD)', f"{sum(d['income'] for d in daily_data.values()):,.2f}"])
        writer.writerow(['總下發 (HKD)', f"{sum(d['expense'] for d in daily_data.values()):,.2f}"])
        writer.writerow(['最終累積結餘 (HKD)', f"{cumulative_balance:,.2f}"])
        writer.writerow(['最終累積結餘 (USDT)', f"{cumulative_balance / exchange_rate:.2f}"])
    
    return filepath

# ========== Telegram 命令 ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    fee_rate, exchange_rate = get_current_rates()
    text = f"""💼 財務公司記帳機器人

當前設定:
📊 費率: {fee_rate}%
💱 匯率: {exchange_rate} HKD/USDT

記帳方式 (只能引用):

1️⃣ 入款: 引用客戶訊息，輸入 +金額
2️⃣ 下發: 引用客戶訊息，輸入 -金額

查看報表:
/list - 今日明細 + 累積結餘
/stats - 今日統計
/export - 匯出 Excel 報表
/undo - 撤銷最後一筆

管理設定:
/fee 3.5 - 設置費率
/rate 7.9 - 設置匯率"""
    await update.message.reply_text(text)

async def set_fee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        await update.message.reply_text("❌ 只有群組管理員才能使用此功能")
        return
    if not context.args:
        await update.message.reply_text("請輸入費率，例如: /fee 3.5")
        return
    try:
        new_fee = float(context.args[0])
        update_rates(fee_rate=new_fee)
        await update.message.reply_text(f"✅ 費率已更新為: {new_fee}%")
    except:
        await update.message.reply_text("❌ 請輸入有效的數字")

async def set_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        await update.message.reply_text("❌ 只有群組管理員才能使用此功能")
        return
    if not context.args:
        await update.message.reply_text("請輸入匯率，例如: /rate 7.9")
        return
    try:
        new_rate = float(context.args[0])
        update_rates(exchange_rate=new_rate)
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

async def show_stats_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """只顯示統計（記帳後用）"""
    stats = get_today_stats()
    fee_rate, exchange_rate = get_current_rates()
    total_income_all, total_expense_all = get_all_stats()
    today_balance = stats['income_hkd'] - stats['expense_hkd']
    today_balance_u = today_balance / exchange_rate
    cumulative_balance = total_income_all - total_expense_all
    cumulative_balance_u = cumulative_balance / exchange_rate
    
    text = f"""📊 今日統計
今日入款: {stats['income_hkd']:,.1f} HKD
今日下發: {stats['expense_hkd']:,.1f} HKD
今日結餘: {today_balance:,.1f} HKD

📈 累積結餘
總入款金額: {total_income_all:,.1f} HKD
總下發金額: {total_expense_all:,.1f} HKD
費率: {fee_rate}%
固定匯率: {exchange_rate}

累計應下發: {total_income_all:,.1f} | {total_income_all / exchange_rate:.2f} u
累計已下發: {total_expense_all:.0f} | {total_expense_all / exchange_rate:.2f} u
累計未下發: {cumulative_balance:,.1f} | {cumulative_balance_u:.2f} u"""
    await update.message.reply_text(text)

async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """顯示完整明細（今日入款/下發逐筆 + 統計）"""
    transactions = get_today_transactions()
    fee_rate, exchange_rate = get_current_rates()
    total_income_all, total_expense_all = get_all_stats()
    cumulative_balance = total_income_all - total_expense_all
    cumulative_balance_u = cumulative_balance / exchange_rate
    
    if transactions:
        incomes = []
        expenses = []
        for type, hkd, usdt, customer, operator, date in transactions:
            time = date.split()[1][:5] if date else ""
            if type == 'income':
                incomes.append((time, hkd, usdt, customer, operator))
            else:
                expenses.append((time, hkd, usdt, customer, operator))
        stats = get_today_stats()
        income_today = stats['income_hkd']
        expense_today = stats['expense_hkd']
        text = ""
        if incomes:
            text += f"今日入款（{len(incomes)}笔）\n"
            for time, hkd, usdt, customer, operator in incomes:
                text += f"{time}  {hkd:.0f}*{1 - fee_rate/100:.3f} / {exchange_rate}={usdt:.2f}U   {customer}  {operator}\n"
            text += "\n"
        if expenses:
            text += f"今日下发（{len(expenses)}笔）\n"
            for time, hkd, usdt, customer, operator in expenses:
                text += f"{time}  {hkd:.0f} / {exchange_rate}={usdt:.2f}U   {customer}  {operator}\n"
            text += "\n"
        text += f"📊 今日統計\n"
        text += f"今日入款: {income_today:,.1f} HKD\n"
        text += f"今日下發: {expense_today:,.1f} HKD\n"
        text += f"今日結餘: {income_today - expense_today:,.1f} HKD\n\n"
        text += f"📈 累積結餘\n"
        text += f"總入款金額: {total_income_all:,.1f} HKD\n"
        text += f"總下發金額: {total_expense_all:,.1f} HKD\n"
        text += f"費率: {fee_rate}%\n"
        text += f"固定匯率: {exchange_rate}\n\n"
        text += f"累計應下發: {total_income_all:,.1f} | {total_income_all / exchange_rate:.2f} u\n"
        text += f"累計已下發: {total_expense_all:.0f} | {total_expense_all / exchange_rate:.2f} u\n"
        text += f"累計未下發: {cumulative_balance:,.1f} | {cumulative_balance_u:.2f} u"
        await update.message.reply_text(text)
    else:
        text = f"📊 今日無交易記錄\n\n📈 累積結餘\n總入款金額: {total_income_all:,.1f} HKD\n總下發金額: {total_expense_all:,.1f} HKD\n費率: {fee_rate}%\n固定匯率: {exchange_rate}\n\n累計應下發: {total_income_all:,.1f} | {total_income_all / exchange_rate:.2f} u\n累計已下發: {total_expense_all:.0f} | {total_expense_all / exchange_rate:.2f} u\n累計未下發: {cumulative_balance:,.1f} | {cumulative_balance_u:.2f} u"
        await update.message.reply_text(text)

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """今日統計（保留兼容）"""
    await show_stats_only(update, context)

async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 正在生成報表，請稍候...")
    filepath = export_to_csv()
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
    if not await is_admin(update):
        await update.message.reply_text("❌ 只有群組管理員才能使用此功能")
        return
    last = get_last_transaction()
    if not last:
        await update.message.reply_text("❌ 沒有找到可撤銷的交易")
        return
    tid, ttype, amount, customer = last
    type_text = "入款" if ttype == 'income' else "下發"
    if cancel_transaction(tid):
        await update.message.reply_text(f"✅ 已撤銷最後一筆{type_text}: {amount:,.2f} HKD (客戶: {customer})")
    else:
        await update.message.reply_text("❌ 撤銷失敗")

async def handle_quick_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        await update.message.reply_text("❌ 只有群組管理員才能使用記帳功能")
        return
    text = update.message.text.strip()
    fee_rate, exchange_rate = get_current_rates()
    operator = update.effective_user.first_name or update.effective_user.username or "管理員"
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ 請引用客戶的訊息來記帳\n\n例如：引用客戶的訊息後輸入 +5000 或 -3000")
        return
    replied_user = update.message.reply_to_message.from_user
    customer = replied_user.first_name or replied_user.username or "未知客戶"
    match = re.match(r'^\+(\d+(?:\.\d+)?)$', text)
    if match:
        amount_hkd = float(match.group(1))
        amount_usdt = calculate_income(amount_hkd, fee_rate, exchange_rate)
        add_transaction('income', amount_hkd, amount_usdt, customer, operator)
        await show_stats_only(update, context)
        return
    match = re.match(r'^-(\d+(?:\.\d+)?)$', text)
    if match:
        amount_hkd = float(match.group(1))
        amount_usdt = calculate_expense(amount_hkd, exchange_rate)
        add_transaction('expense', amount_hkd, amount_usdt, customer, operator)
        await show_stats_only(update, context)
        return
    await update.message.reply_text("❌ 格式錯誤\n\n正確格式：\n引用客戶訊息後輸入：\n+金額  → 入款\n-金額  → 下發\n\n例如：+5000 或 -3000")

# ========== 主程式 ==========
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("fee", set_fee))
    app.add_handler(CommandHandler("rate", set_exchange))
    app.add_handler(CommandHandler("stats", show_stats))
    app.add_handler(CommandHandler("list", show_list))
    app.add_handler(CommandHandler("export", export_excel))
    app.add_handler(CommandHandler("undo", cancel_last))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quick_input))
    print("🤖 財務記帳機器人啟動中...")
    print(f"✅ 當前費率: {get_current_rates()[0]}% | 匯率: {get_current_rates()[1]}")
    print(f"✅ 當前時間: {get_hk_time_str()}")
    print("📝 記帳方式: 只能引用客戶訊息")
    print("🔐 權限設定: 只有群組管理員才能記帳")
    app.run_polling()

if __name__ == "__main__":
    main()
