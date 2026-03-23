import sqlite3
import re
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ========== 配置 ==========
TOKEN = "8793428189:AAE2XwVM_rtl3m4PzV9xUSe3T9gMEvpDEps"  # 改成你的 Token
DEFAULT_FEE_RATE = 3.5
DEFAULT_EXCHANGE_RATE = 7.9

# ========== 資料庫初始化 ==========
def init_db():
    conn = sqlite3.connect('finance.db')
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, amount_hkd REAL, amount_usdt REAL, customer TEXT, operator TEXT, date TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY AUTOINCREMENT, fee_rate REAL, exchange_rate REAL, updated_at TEXT)')
    c.execute("SELECT COUNT(*) FROM settings")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO settings (fee_rate, exchange_rate, updated_at) VALUES (?, ?, ?)", (DEFAULT_FEE_RATE, DEFAULT_EXCHANGE_RATE, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_current_rates():
    conn = sqlite3.connect('finance.db')
    c = conn.cursor()
    c.execute("SELECT fee_rate, exchange_rate FROM settings ORDER BY id DESC LIMIT 1")
    result = c.fetchone()
    conn.close()
    return result if result else (DEFAULT_FEE_RATE, DEFAULT_EXCHANGE_RATE)

def update_rates(fee_rate=None, exchange_rate=None):
    conn = sqlite3.connect('finance.db')
    c = conn.cursor()
    current_fee, current_exchange = get_current_rates()
    new_fee = fee_rate if fee_rate is not None else current_fee
    new_exchange = exchange_rate if exchange_rate is not None else current_exchange
    c.execute("INSERT INTO settings (fee_rate, exchange_rate, updated_at) VALUES (?, ?, ?)", (new_fee, new_exchange, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def calculate_income(amount_hkd, fee_rate, exchange_rate):
    """入款：港幣 × (1 - 費率%) ÷ 匯率 = USDT"""
    return amount_hkd * (1 - fee_rate / 100) / exchange_rate

def calculate_expense(amount_hkd, exchange_rate):
    """下發：港幣 ÷ 匯率 = USDT"""
    return amount_hkd / exchange_rate

def add_transaction(type, amount_hkd, amount_usdt, customer, operator):
    conn = sqlite3.connect('finance.db')
    c = conn.cursor()
    c.execute("INSERT INTO transactions (type, amount_hkd, amount_usdt, customer, operator, date) VALUES (?, ?, ?, ?, ?, ?)", 
              (type, amount_hkd, amount_usdt, customer, operator, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_today_transactions():
    conn = sqlite3.connect('finance.db')
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT type, amount_hkd, amount_usdt, customer, operator, date FROM transactions WHERE date LIKE ? ORDER BY date ASC", (f"{today}%",))
    results = c.fetchall()
    conn.close()
    return results

def get_today_stats():
    conn = sqlite3.connect('finance.db')
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    
    c.execute("SELECT SUM(amount_hkd), SUM(amount_usdt) FROM transactions WHERE type = 'income' AND date LIKE ?", (f"{today}%",))
    income_hkd, income_usdt = c.fetchone() or (0, 0)
    
    c.execute("SELECT SUM(amount_hkd), SUM(amount_usdt) FROM transactions WHERE type = 'expense' AND date LIKE ?", (f"{today}%",))
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

def get_last_transaction():
    conn = sqlite3.connect('finance.db')
    c = conn.cursor()
    c.execute("SELECT id, type, amount_hkd, customer FROM transactions ORDER BY date DESC LIMIT 1")
    result = c.fetchone()
    conn.close()
    return result

def cancel_transaction(transaction_id):
    conn = sqlite3.connect('finance.db')
    c = conn.cursor()
    c.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    conn.commit()
    affected = c.rowcount
    conn.close()
    return affected > 0

# ========== Sample 數據 ==========
SAMPLE_INCOMES = [
    ("17:43:46", 10000, 1221.52, "Bella", "Bella"),
    ("21:58:08", 5000, 610.76, "Wilson", "Wilson"),
    ("18:34:44", 4000, 488.61, "Bella", "Bella"),
    ("23:54:13", 3000, 366.46, "Wilson", "Wilson"),
    ("11:50:35", 1500, 183.23, "Wilson", "Wilson"),
]

SAMPLE_EXPENSES = [
    ("12:32:55", 9000, 1139.24, "大隻佬", "大隻佬"),
    ("14:36:10", 885, 112.03, "大隻佬", "大隻佬"),
    ("14:59:45", 1800, 227.85, "大隻佬", "大隻佬"),
    ("21:57:10", 7000, 886.08, "陳偉霆", "陳偉霆"),
    ("14:05:14", 3000, 379.75, "Bella", "Bella"),
]

# ========== Telegram 命令 ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    fee_rate, exchange_rate = get_current_rates()
    text = f"""💼 *財務公司記帳機器人*

*當前設定：*
📊 費率：{fee_rate}%
💱 匯率：{exchange_rate} HKD/USDT

*記帳方式：*

1️⃣ *入款*：引用客戶訊息，輸入 `+金額`
   💰 例如：引用「強」的訊息，輸入 +5000

2️⃣ *下發*：引用客戶訊息，輸入 `-金額`
   💸 例如：引用「B」的訊息，輸入 -5000

3️⃣ *也可直接輸入*：
   `+5000 強` - 入款5000，客戶強
   `-5000 B` - 下發5000，客戶B

*查看報表：*
`/list` - 今日明細
`/stats` - 今日統計
`/undo` - 撤銷最後一筆

*管理設定：*
`/fee 3.5` - 設置費率
`/rate 7.9` - 設置匯率"""
    await update.message.reply_text(text, parse_mode='Markdown')

async def set_fee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("請輸入費率，例如：/fee 3.5")
        return
    try:
        new_fee = float(context.args[0])
        update_rates(fee_rate=new_fee)
        await update.message.reply_text(f"✅ 費率已更新為：{new_fee}%")
    except:
        await update.message.reply_text("❌ 請輸入有效的數字")

async def set_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("請輸入匯率，例如：/rate 7.9")
        return
    try:
        new_rate = float(context.args[0])
        update_rates(exchange_rate=new_rate)
        await update.message.reply_text(f"✅ 匯率已更新為：{new_rate}")
    except:
        await update.message.reply_text("❌ 請輸入有效的數字")

async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """顯示明細 - 有數據顯示真實數據，沒數據顯示 sample"""
    transactions = get_today_transactions()
    fee_rate, exchange_rate = get_current_rates()
    
    # 如果有真實數據，顯示真實數據
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
        income_total = stats['income_hkd']
        expense_total = stats['expense_hkd']
        
        ying_xiafa = income_total
        yi_xiafa = expense_total
        wei_xiafa = ying_xiafa - yi_xiafa
        
        ying_xiafa_u = ying_xiafa / exchange_rate
        yi_xiafa_u = yi_xiafa / exchange_rate
        wei_xiafa_u = wei_xiafa / exchange_rate
        
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
        
        text += f"总入款金额：{income_total:,.1f}\n"
        text += f"费率：{fee_rate}%\n"
        text += f"固定汇率：{exchange_rate}\n\n"
        text += f"应下发：{ying_xiafa:,.1f} | {ying_xiafa_u:.2f} u\n"
        text += f"已下发：{yi_xiafa:.0f} | {yi_xiafa_u:.2f} u\n"
        text += f"未下发：{wei_xiafa:,.1f} | {wei_xiafa_u:.2f} u"
        
        await update.message.reply_text(text)
    
    else:
        # 沒有數據時，顯示 sample 格式
        text = "今日入款（17笔）\n"
        for time, hkd, usdt, customer, operator in SAMPLE_INCOMES:
            text += f"{time}  {hkd}*0.965 / 7.9={usdt:.2f}U   {customer}  {operator}\n"
        
        text += "\n今日下发（10笔）\n"
        for time, hkd, usdt, customer, operator in SAMPLE_EXPENSES:
            text += f"{time}  {hkd} / 7.9={usdt:.2f}U   {customer}  {operator}\n"
        
        text += "\n总入款金额：167513.4\n"
        text += "费率：3.50%\n"
        text += "固定汇率：7.9\n\n"
        text += "应下发：165423.9 | 20939.73 u\n"
        text += "已下发：57035 | 7219.62 u\n"
        text += "未下发：108388.9 | 13720.11 u"
        
        await update.message.reply_text(text)

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """顯示統計 - 有數據顯示真實數據，沒數據顯示 sample"""
    stats = get_today_stats()
    fee_rate, exchange_rate = get_current_rates()
    
    # 如果有真實數據
    if stats['income_hkd'] > 0 or stats['expense_hkd'] > 0:
        ying_xiafa = stats['income_hkd']
        yi_xiafa = stats['expense_hkd']
        wei_xiafa = ying_xiafa - yi_xiafa
        
        ying_xiafa_u = ying_xiafa / exchange_rate
        yi_xiafa_u = yi_xiafa / exchange_rate
        wei_xiafa_u = wei_xiafa / exchange_rate
        
        text = f"""📊 *今日財務報表*
日期：{datetime.now().strftime("%Y-%m-%d")}
─────────────────
💰 *總入款*
港幣：{stats['income_hkd']:,.2f} HKD
USDT：{stats['income_usdt']:,.2f} U

💸 *總下發*
港幣：{stats['expense_hkd']:,.2f} HKD
USDT：{stats['expense_usdt']:,.2f} U

─────────────────
📈 *結算*
應下發：{ying_xiafa:,.2f} HKD | {ying_xiafa_u:.2f} U
已下發：{yi_xiafa:,.2f} HKD | {yi_xiafa_u:.2f} U
未下發：{wei_xiafa:,.2f} HKD | {wei_xiafa_u:.2f} U

─────────────────
⚙️ *設定*
費率：{fee_rate}% | 匯率：{exchange_rate}"""
        await update.message.reply_text(text, parse_mode='Markdown')
    else:
        # 沒有數據時顯示 sample 統計
        text = f"""📊 *今日財務報表*
日期：{datetime.now().strftime("%Y-%m-%d")}
─────────────────
💰 *總入款*
港幣：167,513.40 HKD
USDT：21,203.59 U

💸 *總下發*
港幣：57,035.00 HKD
USDT：7,219.62 U

─────────────────
📈 *結算*
應下發：167,513.40 HKD | 21,203.59 U
已下發：57,035.00 HKD | 7,219.62 U
未下發：110,478.40 HKD | 13,983.97 U

─────────────────
⚙️ *設定*
費率：{fee_rate}% | 匯率：{exchange_rate}"""
        await update.message.reply_text(text, parse_mode='Markdown')

async def cancel_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    last = get_last_transaction()
    if not last:
        await update.message.reply_text("❌ 沒有找到可撤銷的交易")
        return
    tid, ttype, amount, customer = last
    type_text = "入款" if ttype == 'income' else "下發"
    if cancel_transaction(tid):
        await update.message.reply_text(f"✅ 已撤銷最後一筆{type_text}：{amount:,.2f} HKD（客戶：{customer}）")
    else:
        await update.message.reply_text("❌ 撤銷失敗")

async def handle_quick_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理記帳 - 引用模式"""
    text = update.message.text.strip()
    fee_rate, exchange_rate = get_current_rates()
    
    # 獲取操作人
    operator = update.effective_user.first_name or update.effective_user.username or "管理員"
    
    # 檢查是否引用了別人的訊息
    customer = None
    if update.message.reply_to_message:
        replied_user = update.message.reply_to_message.from_user
        customer = replied_user.first_name or replied_user.username or "未知客戶"
    
    # 模式1: +10000 (入款 HKD，引用模式)
    match = re.match(r'^\+(\d+(?:\.\d+)?)$', text)
    if match:
        amount_hkd = float(match.group(1))
        amount_usdt = calculate_income(amount_hkd, fee_rate, exchange_rate)
        
        if not customer:
            await update.message.reply_text("❌ 請引用客戶的訊息，或直接輸入：+10000 客戶名")
            return
        
        add_transaction('income', amount_hkd, amount_usdt, customer, operator)
        await update.message.reply_text(
            f"{datetime.now().strftime('%H:%M:%S')}  {amount_hkd:.0f}*{1 - fee_rate/100:.3f} / {exchange_rate}={amount_usdt:.2f}U   {customer}  {operator}"
        )
        return
    
    # 模式2: -500 (下發 HKD，引用模式)
    match = re.match(r'^-(\d+(?:\.\d+)?)$', text)
    if match:
        amount_hkd = float(match.group(1))
        amount_usdt = calculate_expense(amount_hkd, exchange_rate)
        
        if not customer:
            await update.message.reply_text("❌ 請引用客戶的訊息，或直接輸入：-5000 客戶名")
            return
        
        add_transaction('expense', amount_hkd, amount_usdt, customer, operator)
        await update.message.reply_text(
            f"{datetime.now().strftime('%H:%M:%S')}  {amount_hkd:.0f} / {exchange_rate}={amount_usdt:.2f}U   {customer}  {operator}"
        )
        return
    
    # 模式3: +10000 Bella (直接輸入)
    match = re.match(r'^\+(\d+(?:\.\d+)?)\s+(.+)$', text)
    if match:
        amount_hkd = float(match.group(1))
        customer = match.group(2).strip()
        amount_usdt = calculate_income(amount_hkd, fee_rate, exchange_rate)
        add_transaction('income', amount_hkd, amount_usdt, customer, operator)
        await update.message.reply_text(
            f"{datetime.now().strftime('%H:%M:%S')}  {amount_hkd:.0f}*{1 - fee_rate/100:.3f} / {exchange_rate}={amount_usdt:.2f}U   {customer}  {operator}"
        )
        return
    
    # 模式4: -5000 大隻佬 (直接輸入)
    match = re.match(r'^-(\d+(?:\.\d+)?)\s+(.+)$', text)
    if match:
        amount_hkd = float(match.group(1))
        customer = match.group(2).strip()
        amount_usdt = calculate_expense(amount_hkd, exchange_rate)
        add_transaction('expense', amount_hkd, amount_usdt, customer, operator)
        await update.message.reply_text(
            f"{datetime.now().strftime('%H:%M:%S')}  {amount_hkd:.0f} / {exchange_rate}={amount_usdt:.2f}U   {customer}  {operator}"
        )
        return

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("fee", set_fee))
    app.add_handler(CommandHandler("rate", set_exchange))
    app.add_handler(CommandHandler("stats", show_stats))
    app.add_handler(CommandHandler("list", show_list))
    app.add_handler(CommandHandler("undo", cancel_last))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quick_input))
    print("🤖 財務記帳機器人啟動中...")
    app.run_polling()

if __name__ == "__main__":
    main()