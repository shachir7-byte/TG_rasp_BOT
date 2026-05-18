import asyncio
import aiohttp
from datetime import datetime, timedelta

# Настройки
API_BASE = "https://sielom.ru/schedule/api"
GROUP_ID = "f7fab01c-26fa-11ee-9f9a-c08a77b51a65" # ИС-23/9-3

async def check_api():
    print("🔍 ТЕСТИРОВАНИЕ API SIELOM.RU\n")
    
    async with aiohttp.ClientSession() as session:
        # 1. Проверка получения списка недель
        print("1. Запрос списка всех недель (/weeks)...")
        try:
            async with session.get(f"{API_BASE}/weeks", timeout=10) as resp:
                if resp.status == 200:
                    weeks = await resp.json()
                    print(f"   ✅ Статус: {resp.status}")
                    print(f"   📦 Получено недель: {len(weeks) if isinstance(weeks, list) else 'Не список'}")
                    if isinstance(weeks, list) and len(weeks) > 0:
                        print("   📅 Последние 3 недели из списка:")
                        for w in weeks[-3:]:
                            print(f"      - ID: {w.get('id')}, с {w.get('dateStart')} по {w.get('dateEnd')}")
                else:
                    print(f"   ❌ Статус: {resp.status}")
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")

        print("\n" + "-"*30 + "\n")

        # 2. Проверка получения week_id по конкретной дате (сегодня и след. понедельник)
        today = datetime.now().strftime("%Y-%m-%d")
        next_monday = (datetime.now() + timedelta(days=(7 - datetime.now().weekday()) % 7 + 1)).strftime("%Y-%m-%d")
        
        for date_str, label in [(today, "Сегодня"), (next_monday, "След. Понедельник")]:
            print(f"2. Запрос week_id для даты {label} ({date_str})...")
            try:
                url = f"{API_BASE}/weeks/date/{date_str}"
                async with session.get(url, timeout=10) as resp:
                    text = await resp.text()
                    print(f"   🌐 URL: {url}")
                    print(f"   ✅ Статус: {resp.status}")
                    print(f"   📄 Ответ сервера: {text[:200]}") # Первые 200 символов
                    
                    if resp.status == 200:
                        data = await resp.json()
                        if data:
                            print(f"   🎯 Найден week_id: {data[0].get('id') if isinstance(data, list) else data.get('id')}")
                        else:
                            print("   ⚠️ Ответ пустой (список пуст)")
                    else:
                        print("   ⚠️ Не удалось получить данные")
            except Exception as e:
                print(f"   ❌ Ошибка: {e}")
            
            print()

        print("-"*30 + "\n")

        # 3. Прямой запрос расписания с известным ID (если есть) или тестовый
        print("3. Попытка получить расписание группы...")
        # Попробуем взять ID из первой доступной недели, если список недель получен
        # Для теста возьмем дату сегодня и попробуем найти неделю перебором, если прямой не сработал
        
        # Сначала попробуем просто дернуть эндпоинт группы без week_id (иногда работает) или с фейковым
        # Но лучше найдем реальную дату, где есть пары. 
        # Попробуем запросить расписание на сегодня, подобрав неделю из списка (если шаг 1 сработал)
        
        # Временно используем заглушку, чтобы не усложнять код теста
        print("   ℹ️ Для точного теста расписания нужен валидный week_id.")
        print("   ℹ️ Если в пункте 1 список недель пуст — значит, на сервере нет будущих недель.")
        
        # Дополнительно: проверим, доступен ли сервер вообще
        try:
            async with session.get("https://sielom.ru", timeout=5) as resp:
                print(f"\n4. Доступность главного сайта: {'✅ OK' if resp.status == 200 else '❌ НЕТ'}")
        except:
            print("\n4. Доступность главного сайта: ❌ Сайт недоступен")

if __name__ == "__main__":
    asyncio.run(check_api())