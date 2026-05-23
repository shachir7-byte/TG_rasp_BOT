import os

# Папки и файлы, которые нужно игнорировать
IGNORE_FOLDERS = {'venv', '__pycache__', '.git', 'доп'}
IGNORE_FILES = {'.db', '.log', '.png', '.jpg', '.env', '.pyc', '.db-shm', '.db-wal'}
OUTPUT_FILE = 'project_backup.txt'

def should_ignore(path):
    # Игнорируем папки
    parts = path.split(os.sep)
    if any(part in IGNORE_FOLDERS for part in parts):
        return True
    # Игнорируем файлы по расширению
    _, ext = os.path.splitext(path)
    if ext in IGNORE_FILES:
        return True
    return False

def collect_code():
    output = []
    output.append("=" * 50)
    output.append("BACKUP OF TELEGRAM BOT PROJECT")
    output.append("=" * 50)
    output.append("")

    for root, dirs, files in os.walk("."):
        # Фильтрация папок на лету, чтобы не заходить в venv
        dirs[:] = [d for d in dirs if d not in IGNORE_FOLDERS]
        
        for file in files:
            if file.endswith('.py'):
                full_path = os.path.join(root, file)
                if not should_ignore(full_path):
                    output.append(f"\n{'='*20} FILE: {full_path} {'='*20}\n")
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            output.append(content)
                    except Exception as e:
                        output.append(f"Error reading file: {e}")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("\n".join(output))
    
    print(f"✅ Бэкап успешно создан в файле: {OUTPUT_FILE}")
    print(f"📂 Размер файла: {os.path.getsize(OUTPUT_FILE)} байт")

if __name__ == "__main__":
    collect_code()