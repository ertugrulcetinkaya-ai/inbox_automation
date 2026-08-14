# Inbox Automation

`inbox_automation`, `ertugrul@cetinkayalar.com` hesabının Apple Mail gelen kutusunu okuyup toplantı bilgilerini Telegram üzerinden özetleyen, makineden bağımsız bir otomasyondur.

Proje şu anda yalnızca hedef hesabın birincil gelen kutusunu ve son 30 gündeki mesajları inceler. Okundu/okunmadı durumu önemli değildir; hiçbir mesaj değiştirilmez.

## Ne yapar?

- Türkçe ve İngilizce toplantı ifadelerini, tarihleri ve saatleri algılar.
- `Bugün` ve `yarın` gibi göreli ifadeleri mesajın alındığı tarihe göre yorumlar.
- Her gün saat 08:00'de o gün yapılacak toplantıları Telegram'a gönderir.
- O gün toplantı yoksa `Bugün toplantı yok.` mesajını gönderir.
- Gelecek toplantıları bugünden başlayarak listeleyebilir.
- LLM kullanmadan, deterministik tarih/saat ayrıştırması yapar.

## Telegram komutları

Hermes Gateway aktifken aşağıdaki komutlar Telegram'da kullanılabilir:

| Amaç | Türkçe komutlar | ASCII eşdeğerleri |
|---|---|---|
| Bugünün toplantıları | `/bugün`, `/toplantılar` | `/bugun`, `/toplantilar` |
| Bugün ve sonraki toplantılar | `/gelecek_toplantılar`, `/toplantılar_gelecek`, `/sonraki_toplantılar` | `/gelecek_toplantilar`, `/toplantilar_gelecek`, `/sonraki_toplantilar` |
| Servis durumu | `/durum` | `/status` |

İleri tarihli listeyi yerel olarak kontrol etmek için:

```bash
.venv/bin/python main.py --upcoming --dry-run
```

## Mimari

- `mail_fetcher.applescript`: Apple Mail'den yalnızca hedef hesabın birincil Inbox'ını okur.
- `main.py`: Mesajları temizler, toplantıları ayrıştırır ve günlük/gelecek özetini üretir.
- `telegram_listener.py`: Yalnızca bağımsız kurulumlarda kullanılan Telegram listener'ıdır.
- `scripts/install_launchd.py`: Makineye göre launchd plist dosyalarını üretir.
- `HERMES_PROJECT_MEMORY.md`: Hermes için operasyonel proje hafızasıdır.
- `AGENTS.md`: Bu projede değişiklik yaparken uyulacak kurallardır.

AppleScript JSON üretmez; güvenli `__MAIL_DIGEST_FIELD__` ayırıcısını kullanır. Temizleme ve ayrıştırma Python tarafında yapılır. Böylece e-posta içindeki tırnak, ters bölü ve kontrol karakterleri Telegram çıktısını bozmaz.

## Kurulum ve yerel kullanım

Telegram bilgileri `~/.hermes_local_automation/telegram.env` dosyasında tutulur:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py --dry-run
```

Apple Mail açık olmalı ve terminale Mail otomasyon izni verilmelidir. `TELEGRAM_ENV_FILE` ile kimlik bilgisi dosyası, `COMPANY_REPORT_ROOT` ile Company Reporting checkout yolu değiştirilebilir.

## Çalıştırma modeli

- GitHub `main` dalı kanonik kaynaktır.
- Mac Studio ve MacBook Pro geliştirme içindir.
- Mac Mini tek runtime/production makinesidir.
- Company Reporting/Hermes aynı Telegram botunu yönetirken bağımsız `telegram_listener.py` çalıştırılmaz; ikinci Telegram polling süreci oluşturulmaz.
- Mac Mini deploy'u `company_reporting_hub/scripts/deploy_mac_mini.sh` ile yapılır.

## Test

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python -m py_compile main.py telegram_listener.py
git diff --check
```

Mail erişimi ve Telegram gönderimi salt-okunur operasyon mantığıyla tasarlanmıştır: mesaj silme, taşıma, işaretleme, cevaplama, yönlendirme veya taslak oluşturma yapılmaz.
