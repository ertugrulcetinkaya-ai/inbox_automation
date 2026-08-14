# Inbox Automation

`inbox_automation`, `ertugrul@cetinkayalar.com` hesabının Apple Mail gelen kutusunu okuyup toplantı bilgilerini Telegram üzerinden özetleyen, makineden bağımsız bir otomasyondur.

Proje şu anda yalnızca hedef hesabın birincil gelen kutusunu ve son 30 gündeki mesajları inceler. Okundu/okunmadı durumu önemli değildir; hiçbir mesaj değiştirilmez.

## Ne yapar?

- Türkçe ve İngilizce toplantı ifadelerini, tarihleri ve saatleri algılar.
- Önce gerçek iCalendar/ICS verisini parse eder; ICS yoksa semantic metin parser'ına düşer.
- `Bugün` ve `yarın` gibi göreli ifadeleri mesajın alındığı tarihe göre yorumlar.
- Haftanın günlerini çıplak (`Cuma`) veya `bu/önümüzdeki` ve `this/next` gibi göreli ifadelerle gelecek uygun tarihe çözer.
- Yıl içermeyen tarihlerde, tarih hedef günden en az 60 gün geçmişse bir sonraki yıl değerlendirilir; örneğin 20 Aralık'ta geçen `5 Ocak`, 5 Ocak 2027 kabul edilir.
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

- `mail_fetcher.applescript`: Apple Mail'den yalnızca hedef hesabın birincil Inbox'ını okur; subject yanında body calendar sinyallerini arar, satır sonlarını korur ve mümkünse ham MIME kaynağını taşır.
- `main.py`: launchd/Hermes uyumluluğu için ince giriş noktasıdır; mevcut import ve `python main.py` sözleşmesini korur.
- `mail_digest/config.py`, `models.py`, `utils.py`: makine bağımsız ayarlar, canonical `Meeting` modeli ve ortak temizleme yardımcıları.
- `mail_digest/sources/apple_mail.py`: AppleScript'i çalıştırır ve Mail transport kayıtlarını parse eder.
- `mail_digest/parsing/`: tarih, saat, ICS ve semantic meeting parser'larını birbirinden ayırır.
- `mail_digest/services/meeting_service.py`: toplantı deduplikasyonu ve günlük/gelecek özetlerinin render edilmesini yönetir.
- `mail_digest/delivery/telegram.py`: yalnızca Telegram gönderim katmanını içerir; parser katmanına bağımlı değildir.
- `mail_digest/cli.py`: günlük digest CLI akışını yönetir.
- `telegram_listener.py`: Yalnızca bağımsız kurulumlarda kullanılan Telegram listener'ıdır.
- `scripts/install_launchd.py`: Makineye göre launchd plist dosyalarını üretir.
- `HERMES_PROJECT_MEMORY.md`: Hermes için operasyonel proje hafızasıdır.
- `AGENTS.md`: Bu projede değişiklik yaparken uyulacak kurallardır.

### ICS-first veri akışı

```text
Apple Mail
   ↓
candidate message + raw MIME source
   ↓
ICS / MIME calendar parser
   ↓  (ICS yoksa)
semantic text parser
   ↓
canonical Meeting
   ↓
daily / upcoming Telegram digest
```

Kod akışı katmanlıdır: `sources` Mail erişimini, `parsing` toplantı çıkarımını, `services` iş kurallarını, `delivery` Telegram gönderimini ve `cli` çalışma akışını taşır. Böylece tarih/ICS parser değişiklikleri Telegram gönderim koduna dokunmadan test edilebilir.

Canonical `Meeting` modeli şu alanları taşır: `uid`, `title`, `organizer`, `start_at`, `end_at`, `timezone`, `location`, `join_url`, `status`, `source_message_id` ve `confidence`. ICS toplantıları `confidence=1.0` ile gelir; semantic fallback kayıtları daha düşük güven seviyesiyle işaretlenir.

`STATUS:CANCELLED` veya `METHOD:CANCEL` olan ICS etkinlikleri listeye alınmaz. Aynı `UID` için daha yüksek `SEQUENCE` değerine sahip kayıt geçerli kabul edilir; böylece tarih değişikliği ve iptal mailleri eski daveti bastırır. Semantic metinde `iptal`, `ertelendi/rescheduled` ve `tentative` durumları da sınıflandırılır. `DTSTART;TZID=...`, UTC (`Z`) ve tarih-only ICS değerleri desteklenir; UTC zamanları Telegram özeti için Europe/Istanbul saatine çevrilir.

Yıl belirtilmeyen `5 Ocak` gibi tarihler önce hedef tarihin yılıyla oluşturulur. Bu aday hedef tarihten en az 60 gün gerideyse bir sonraki yıl adayı kullanılır; böylece yıl geçişinde gelecek toplantılar elenmezken, yakın geçmişteki tarihler yanlışlıkla ileri taşınmaz.

Haftanın günü ifadeleri mesajın alındığı tarihe göre çözülür. `Cuma` ve `bu/this Cuma` bir sonraki uygun Cuma’yı, `önümüzdeki/next Friday` ise aynı günse takip eden haftadaki ilk Cuma’yı seçer.

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
