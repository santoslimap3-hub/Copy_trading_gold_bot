import asyncio
import MetaTrader5 as mt
from telethon import TelegramClient, events

api_id = 34597981
api_hash = "2cd59609b6cacb56da261e43fdb897ea"

mt.initialize()

symbol = "XAUUSD"
lot = 0.01

mt.symbol_select(symbol, True)

buy_request = {
    "action": mt.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "volume": lot,
    "type": mt.ORDER_TYPE_BUY,
    "price": mt.symbol_info_tick(symbol).ask,
    "sl": round(mt.symbol_info_tick(symbol).ask - 8.0, 2),
    "deviation": 20,
    "magic": 1,
    "comment": "python trade",
    "type_time": mt.ORDER_TIME_GTC,
    "type_filling": mt.ORDER_FILLING_IOC
}

sell_request = {
    "action": mt.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "volume": lot,
    "type": mt.ORDER_TYPE_SELL,
    "price": mt.symbol_info_tick(symbol).ask,
    "sl": round(mt.symbol_info_tick(symbol).bid + 8.0, 2),
    "deviation": 20,
    "magic": 1,
    "comment": "python trade",
    "type_time": mt.ORDER_TIME_GTC,
    "type_filling": mt.ORDER_FILLING_IOC
}
async def main():

    latest_message = ""
    client = TelegramClient("session", api_id, api_hash)

    @client.on(events.NewMessage(chats=["test_bot_for_gold", -1003349563414]))
    async def new_message(event):

        latest_message = event.message.text

        if latest_message == "XAUUSD BUY NOW":
            result = mt.order_send(buy_request)
            print(result)
            
        elif latest_message == "XAUUSD SELL NOW":
            result = mt.order_send(sell_request)
            print(result)
    
    @client.on(events.MessageEdited(chats = ["test_bot_for_gold", -1003349563414]))
    async def on_message_edit(event):
        text = event.text
        if "SL" in text:
            start = text.find("SL") + 3
            result = text[start:start+5]
            print(result)
        elif "TP1" in text:
            start = text.find("SL") + 3
            result = text[start:start+5]
            print(result)
        elif "TP3" in text:
            start = text.find("SL") + 3
            result = text[start:start+5]
            print(result)
    await client.start()
    await client.run_until_disconnected()
    return
asyncio.run(main())