# Inbox Automation

`inbox_automation`, `ertugrul@cetinkayalar.com` hesabının üretim Gmail gelen kutusunu resmi ve salt-okunur Gmail API üzerinden okuyup toplantı bilgilerini Telegram üzerinden özetleyen, makineden bağımsız bir otomasyondur. Apple Mail yalnızca rollback kaynağı olarak korunur. Üretim launchd ortamı `MAIL_SOURCE=gmail` kullanır; ortam değişkeni yoksa kodun güvenli yerel varsayılanı `apple_mail` olarak kalır.

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

- `mail_fetcher.applescript`: rollback kaynağı olarak Apple Mail'den yalnızca hedef hesabın birincil Inbox'ındaki son 30 günlük mesajları okur; Mail'in tüm mailbox üzerinde yavaş çalışan tarih/body sorgularını kullanmak yerine tarih sınırını indeks sırasından bulur, penceredeki her mesaj için gövde okuma denemesi yapar ve takvim daveti sinyali (konu, gövde, `.ics` ek adı veya Türkçe `Davet:` konu öneki) varsa ham MIME kaynağını seçici olarak taşır. Bir mesajın toplantı içerip içermediğine Python karar verir; AppleScript konu bazlı eleme yapmaz.
- `main.py`: launchd/Hermes uyumluluğu için ince giriş noktasıdır; mevcut import ve `python main.py` sözleşmesini korur.
- `mail_digest/config.py`, `models.py`, `utils.py`: makine bağımsız ayarlar, canonical `Meeting` modeli ve ortak temizleme yardımcıları.
- `mail_digest/sources/apple_mail.py`: AppleScript'i çalıştırır ve Mail transport kayıtlarını parse eder.
- `mail_digest/sources/gmail/`: Gmail OAuth, salt-okunur API, MIME normalizasyonu, SQLite cache ve history senkronizasyon katmanıdır.
- `mail_digest/parsing/`: tarih, saat, ICS ve semantic meeting parser'larını birbirinden ayırır.
- `mail_digest/services/meeting_service.py`: toplantı deduplikasyonu ve günlük/gelecek özetlerinin render edilmesini yönetir.
- `mail_digest/services/lock.py`: launchd ve Telegram girişlerini tek digest çalışmasına indiren process-level `fcntl.flock()` kilidini yönetir.
- `mail_digest/delivery/telegram.py`: yalnızca Telegram gönderim katmanını içerir; parser katmanına bağımlı değildir.
- `mail_digest/cli.py`: günlük digest CLI akışını ve ortak kilitli `run_digest()` servis çağrısını yönetir.
- `telegram_listener.py`: Yalnızca bağımsız kurulumlarda kullanılan Telegram listener'ıdır.
- `scripts/install_launchd.py`: Makineye göre launchd plist dosyalarını üretir.
- `HERMES_PROJECT_MEMORY.md`: Hermes için operasyonel proje hafızasıdır.
- `AGENTS.md`: Bu projede değişiklik yaparken uyulacak kurallardır.

### ICS-first veri akışı

```text
Gmail API production kaynağı veya Apple Mail rollback kaynağı (son 30 gün)
   ↓
her mesajın gövdesi + takvim sinyali olanlarda raw MIME source
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

Apple Mail yalnızca yerel rollback çalıştıracaksanız açık olmalı ve terminale Mail otomasyon izni verilmelidir. `TELEGRAM_ENV_FILE` ile kimlik bilgisi dosyası, `COMPANY_REPORT_ROOT` ile Company Reporting checkout yolu değiştirilebilir.

## Gmail API kurulumu ve production kullanımı

Gmail kaynağı yalnızca `https://www.googleapis.com/auth/gmail.readonly` OAuth kapsamını ister. Kod mesajları değiştirmez, etiketlemez, okundu durumunu değiştirmez, taşımaz, silmez veya göndermez. Gmail hatasında Apple Mail'e otomatik fallback yoktur; seçilen kaynak başarısızsa digest de başarısız olur.

1. Google Cloud projesinde Gmail API'yi etkinleştirin ve Desktop application OAuth istemcisi oluşturun. OAuth doğrulamasının tamamlandığı varsayılmaz; bu operatör adımıdır.
2. İndirilen istemci dosyasını `~/.hermes_local_automation/gmail/credentials.json` konumuna koyun ve `chmod 600` uygulayın.
3. Bağımlılıkları `requirements.txt` üzerinden kurduktan sonra bir kez interaktif yetkilendirme çalıştırın:

   ```bash
   .venv/bin/python scripts/gmail_auth.py
   ```

   Normal `main.py` çalışması hiçbir zaman tarayıcı açmaz. Token eksik, geçersiz veya yenilenemiyorsa açık bir hatayla durur.

4. İlk canlı kontrolü production job'ını değiştirmeden yapın:

   ```bash
   MAIL_SOURCE=gmail .venv/bin/python main.py --upcoming --dry-run
   ```

Varsayılan yollar `~/.hermes_local_automation/gmail/token.json` ve `~/.hermes_local_automation/gmail/cache.sqlite3`'tür. Sırasıyla `GMAIL_CREDENTIALS_FILE`, `GMAIL_TOKEN_FILE` ve `GMAIL_CACHE_FILE` ile değiştirilebilir. Bu credential, token ve cache dosyalarını repoya koymayın.

İlk Gmail çalışması, `getProfile` history sınırını snapshot'tan önce alır; 31 günlük sunucu sorgusunu yerel kesin 30×24 saat filtresiyle daraltır, bağımsız staging snapshot oluşturur, snapshot sırasında oluşan history değişikliklerini güncel mesaj durumuyla uzlaştırır ve cache ile checkpoint'i tek SQLite transaction'ında etkinleştirir. Sonraki çalışmalar yalnızca checkpoint sonrasındaki `messagesAdded`, `messagesDeleted`, `labelsAdded` ve `labelsRemoved` değişikliklerini uzlaştırır. Eski history checkpoint'i HTTP 404 verirse güvenli tam sync yeniden başlar. Mesaj içeriği bozuksa yapısal kimlik/INBOX/tarih bilgisi geçerli olduğu sürece boş içerikli degraded kayıt saklanır.

Production transport Gmail'dir: `MAIL_SOURCE=gmail` kullanın. Gmail hatasında Apple Mail'e otomatik fallback yoktur; seçilen kaynak başarısızsa digest başarısız olur. launchd dosyalarını Gmail ayarlarıyla preview olarak üretmek için:

```bash
.venv/bin/python scripts/install_launchd.py --mail-source gmail
```

Komut varsayılan olarak yalnızca preview üretir; gerçek production job bu repository değişikliğiyle Gmail'e çevrilmez.

Rollback gerektiğinde production job'larını açıkça `MAIL_SOURCE=apple_mail` ile yeniden üretip doğrulayın. Rollback geçici bir işletim prosedürüdür; Apple Mail Issue-2 davranışı Gmail yolunun yerine kalıcı production kaynağı değildir.

`gmail.readonly` restricted bir kapsamdır. External OAuth projesi Testing durumunda bırakılırsa bu tür kapsamlar için refresh token'ları 7 günle sınırlı olabilir; uzun süreli unattended kullanım öncesinde Google Auth Platform production yapılandırması operatör tarafından tamamlanmalıdır.

## Çalıştırma modeli

- GitHub `main` dalı kanonik kaynaktır.
- Mac Studio ve MacBook Pro geliştirme içindir.
- Mac Mini tek runtime/production makinesidir.
- Company Reporting/Hermes aynı Telegram botunu yönetirken bağımsız `telegram_listener.py` çalıştırılmaz; ikinci Telegram polling süreci oluşturulmaz.
- 08:00 launchd çalışması ile Telegram komutu aynı `run_digest()` akışını kullanır. `/tmp/mail_unread_digest.lock` dosyası yalnızca kilit buluşma noktasıdır; gerçek kilit `fcntl.flock()` ile tutulur ve process kapanınca kernel tarafından bırakılır. Dosyanın diskte kalması stale-lock oluşturmaz.
- Kilit yolunu farklı bir makine veya çalışma ortamı için `MAIL_DIGEST_LOCK_FILE` ile değiştirebilirsiniz.
- Mac Mini deploy'u `company_reporting_hub/scripts/deploy_mac_mini.sh` ile yapılır.

## Test

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python -m py_compile main.py telegram_listener.py mail_digest/**/*.py tests/*.py
git diff --check
```

Test kapsamı parser matrisi, ICS/deduplication regresyonları, anonim Mail fixture'ları ve dış sistem hata akışlarını içerir. `tests/fixtures/` altında kişisel veri içermeyen 20 örnek bulunur; AppleScript timeout/bozuk çıktı, Telegram HTTP veya `ok:false` cevabı, 10.30/09.30/14.30 saatleri, tarih biçimleri, yıl geçişi, hafta günleri, quoted reply, iptal, erteleme ve çoklu tarih senaryoları test edilir.

Mail erişimi ve Telegram gönderimi salt-okunur operasyon mantığıyla tasarlanmıştır: mesaj silme, taşıma, işaretleme, cevaplama, yönlendirme veya taslak oluşturma yapılmaz.
