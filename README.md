# Inbox Automation

`inbox_automation`, `ertugrul@cetinkayalar.com` hesabının üretim Gmail gelen kutusunu resmi ve salt-okunur Gmail API üzerinden okuyup toplantı bilgilerini Telegram üzerinden özetleyen, makineden bağımsız bir otomasyondur. Yalnızca birincil gelen kutusu (`INBOX`/`Inbox`) taranır. Gmail varsayılan kaynaktır; Apple Mail yalnızca `MAIL_SOURCE=apple_mail` açıkça verildiğinde rollback kaynağı olarak kullanılır.

Proje yalnızca hedef hesabın birincil gelen kutusunu inceler. Gmail'de normal mesajlar 30 gün, takvim payload'ı (`raw_source`) bulunan mesajlar 366 gün saklanır; Apple Mail rollback yolu 30 günle sınırlıdır. Okundu/okunmadı durumu önemli değildir; hiçbir mesaj değiştirilmez.

## Ne yapar?

- Türkçe ve İngilizce toplantı ifadelerini, tarihleri ve saatleri algılar.
- Önce gerçek iCalendar/ICS verisini parse eder; ICS yoksa semantic metin parser'ına düşer.
- Tekrarlayan ICS toplantılarını `RRULE`/`RDATE` ile istenen tarih aralığında açar, `EXDATE` günlerini çıkarır; tekil güncellemeleri ve iptalleri `UID + RECURRENCE-ID` ile eşleştirir. Recurrence kimliği UTC ISO biçiminde normalleştirilir; timezone'suz saatler Europe/Istanbul kabul edilir, tüm gün kimliği tarih olarak kalır. Açılım etkinliğin saat dilimini korur ve seri başına 500 occurrence ile sınırlıdır.
- Upcoming ve dikkat görünümlerinde aynı UID'li recurring seriden yalnızca en yakın occurrence `🔁 Tekrarlayan` etiketiyle gösterilir; günlük, haftalık ve hatırlatma modları tüm occurrence'ları korur.
- Semantic fallback adaylarını ikinci bir bağlam kontrolünden geçirir: açık toplantı konusu tüm-gün kaydı destekleyebilir; yalnızca gövdeden gelen adaylarda toplantı ifadesi, tarih ve saatin yakın olması gerekir. Alıntılanmış `From/Sent/Subject` zincir başlıkları ve dağınık rapor tarihleri toplantı sayılmaz.
- `Bugün` ve `yarın` gibi göreli ifadeleri mesajın alındığı tarihe göre yorumlar.
- Haftanın günlerini çıplak (`Cuma`) veya `bu/önümüzdeki` ve `this/next` gibi göreli ifadelerle gelecek uygun tarihe çözer.
- Yıl içermeyen tarihlerde, tarih hedef günden en az 60 gün geçmişse bir sonraki yıl değerlendirilir; örneğin 20 Aralık'ta geçen `5 Ocak`, 5 Ocak 2027 kabul edilir.
- Her gün saat 08:00'de o gün yapılacak toplantıları Telegram'a gönderir.
- Varsayılan olarak toplantıdan 15 dakika önce konu, saat, yer ve katılım bağlantısını Telegram'a hatırlatır. Reminder taraması son başarılı tarama zamanını saklar; lock/contention nedeniyle atlanan tur bir sonraki taramada catch-up aralığıyla yeniden değerlendirilir. Başarılı gönderim hash'leri overlap kaynaklı tekrarları engeller.
- O gün toplantı yoksa `Bugün toplantı yok.` mesajını gönderir.
- Gelecek toplantıları bugünden başlayarak listeleyebilir.
- Bugün dâhil 7 günlük görünümü günlere göre gruplar; çakışmaları ve 15 dakikadan kısa araları işaretler.
- Ertelenmiş, kesinleşmemiş veya düşük güvenle metinden çıkarılmış toplantıları ayrı bir `Dikkat gerektirenler` bölümünde gösterir.
- LLM kullanmadan, deterministik tarih/saat ayrıştırması yapar.
- Ayrıştırma sonuçlarını yerel SQLite önbelleğinde tekrar kullanır. Önbellek varsayılan olarak `~/.hermes_local_automation/mail_digest/parse_cache.sqlite3` konumunda tutulur; dosya `0600`, dizin `0700` izinleriyle korunur ve `MAIL_DIGEST_PARSE_CACHE_FILE` ile değiştirilebilir. Anahtarlar parser sürümünü, mesaj ayrıştırma girdilerini ve istenen tarih aralığını kapsar. Önbellek kullanılamazsa ayrıştırma normal şekilde sürer.

## Telegram komutları

Bağımsız `telegram_listener.py`, bu bot için tek polling ve komut sahibidir. Toplantı alias'ları `mail_digest/commands.py` tablosundan yönetilir:

### Toplantı komutları

| Mod | Komut |
|---|---|
| `daily` | `/toplantılar` |
| `daily` | `/toplantilar` |
| `daily` | `/bugün` |
| `daily` | `/bugun` |
| `daily` | `/mail` |
| `daily` | `/unread` |
| `upcoming` | `/gelecek_toplantılar` |
| `upcoming` | `/gelecek_toplantilar` |
| `upcoming` | `/toplantılar_gelecek` |
| `upcoming` | `/toplantilar_gelecek` |
| `upcoming` | `/sonraki_toplantılar` |
| `upcoming` | `/sonraki_toplantilar` |
| `weekly` | `/hafta` |
| `weekly` | `/haftalık` |
| `weekly` | `/haftalik` |
| `weekly` | `/week` |
| `attention` | `/dikkat` |
| `attention` | `/attention` |

### Diğer komutlar

- `/help` ve `/komutlar`, önce yukarıdaki toplantı komutlarını gösterir; isteğe bağlı rapor paketi bulunuyorsa rapor komutlarını da ekler.
- `/durum` ve `/status`, her digest modunun son başarı/başarısızlık zamanını, seçili mail kaynağını, Gmail token dosyasının izin ve süre bilgisini, salt-okunur cache yaşını/kayıt sayısını ve son hatırlatma taramasını gösterir.
- `servis*`, `fatura*`, `/session`, `/sys_session` ve `/session_hazirla` komutları yalnızca aynı Python ortamında isteğe bağlı Company Reporting paketi kuruluysa çalışır. Paket yoksa `Rapor servisi bu ortamda hazır değil; toplantı komutları yine kullanılabilir.` yanıtı verilir.

Rapor yalnızca yerel dosyaları okur; OAuth token'ını yenilemez. `main.py --status --dry-run` raporu terminale yazar; `main.py --status` Telegram'a gönderir. Her gerçek (dry-run olmayan) `run_digest()` çağrısı mod başına zamanları varsayılan `~/.hermes_local_automation/mail_digest/status.json` dosyasına atomik ve `0600` izinle kaydeder; dry-run bu dosyayı değiştirmez. Yolu `MAIL_DIGEST_STATUS_FILE` ile değiştirebilirsiniz. Durum kaydı yazılamazsa yalnızca hata sınıfı loglanır ve digest devam eder.

`telegram_listener.run_digest()` dış çağıranlara günlük, gelecek, haftalık ve dikkat özeti için başarı metni döndürür. Listener'ın kendi komut yolu bu başarı onayını tekrar göndermez; hata ve özetin hâlihazırda hazırlanmakta olduğunu bildiren yanıtları iletir.

Telegram özetleri 3.500 karakterlik parçalara ayrılır. Parçalar satır sonlarında bölünür; tek bir satır sınırı aşıyorsa yalnızca o satır sert sınırdan kesilir.

İleri tarihli listeyi yerel olarak kontrol etmek için:

```bash
.venv/bin/python main.py --upcoming --dry-run
.venv/bin/python main.py --week --dry-run
.venv/bin/python main.py --attention --dry-run
.venv/bin/python main.py --reminder --dry-run
```

## Mimari

- `mail_fetcher.applescript`: rollback kaynağı olarak Apple Mail'den yalnızca hedef hesabın birincil Inbox'ındaki son 30 günlük mesajları okur; Mail'in tüm mailbox üzerinde yavaş çalışan tarih/body sorgularını kullanmak yerine tarih sınırını indeks sırasından bulur, penceredeki her mesaj için gövde okuma denemesi yapar ve takvim daveti sinyali (konu, gövde, `.ics` ek adı veya Türkçe `Davet:` konu öneki) varsa ham MIME kaynağını seçici olarak taşır. Bir mesajın toplantı içerip içermediğine Python karar verir; AppleScript konu bazlı eleme yapmaz.
- `main.py`: launchd ve eski çağıranlarla uyumlu ince giriş noktasıdır; mevcut import ve `python main.py` sözleşmesini korur.
- `mail_digest/config.py`, `models.py`, `utils.py`: makine bağımsız ayarlar, canonical `Meeting` modeli ve ortak temizleme yardımcıları.
- `mail_digest/sources/apple_mail.py`: AppleScript'i çalıştırır ve Mail transport kayıtlarını parse eder.
- `mail_digest/sources/gmail/`: Gmail OAuth, salt-okunur API, MIME normalizasyonu, SQLite cache ve history senkronizasyon katmanıdır.
- `mail_digest/parsing/`: tarih, saat, ICS ve semantic meeting parser'larını birbirinden ayırır.
- `mail_digest/services/meeting_service.py`: toplantı deduplikasyonu ve günlük/gelecek özetlerinin render edilmesini yönetir.
- `mail_digest/services/parse_cache.py`: tarih aralığı ve parser sürümüne bağlı SHA-256 anahtarlarla JSON ayrıştırma sonuçlarını SQLite'ta saklar; cache hatası digest'i engellemez.
- `mail_digest/services/reminder_state.py`: başarılı hatırlatmaları hash kimlikleriyle kaydeder ve son başarılı reminder taramasını cursor olarak tutarak aynı toplantı için tekrar bildirim gönderilmesini önler.
- `mail_digest/services/run_status.py` and `mail_digest/services/health.py`: mod başına çalışma durumunu kişisel veri içermeden atomik kaydeder ve ağ kullanmadan `/durum`/`--status` raporunu oluşturur. Gmail token JSON'u yenileme yapmadan okunur; SQLite cache salt-okunur açılır.
- `mail_digest/services/lock.py`: launchd ve Telegram girişlerini tek digest çalışmasına indiren process-level `fcntl.flock()` kilidini yönetir.
- `mail_digest/services/log_rotation.py`: CLI ve bağımsız listener başlarken yalnızca kendi launchd log adlarını `PROJECT_ROOT/logs` altında, 5 MiB'tan büyükler için en fazla üç arşivle ve hata yutacak şekilde döndürür.
- `mail_digest/delivery/telegram.py`: yalnızca Telegram gönderim katmanını içerir; parser katmanına bağımlı değildir.
- `mail_digest/cli.py`: günlük digest CLI akışını ve ortak kilitli `run_digest()` servis çağrısını yönetir.
- `telegram_listener.py`: Yalnızca bağımsız kurulumlarda kullanılan Telegram listener'ıdır.
- `scripts/install_launchd.py`: Makineye göre launchd plist dosyalarını üretir.
- `AGENTS.md`: Ajanların uygulama ve doğrulama kurallarıdır.

### ICS-first veri akışı

```text
Gmail API production kaynağı (normal mesajlar 30 gün, takvim payload'ı 366 gün) veya Apple Mail rollback kaynağı (30 gün)
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

Canonical `Meeting` modeli aşağıdaki alanları taşır. ICS toplantıları `confidence=1.0` ile gelir; ikinci bağlam kontrolünü geçen semantic fallback kayıtları daha düşük güven seviyesiyle işaretlenir ve `Dikkat gerektirenler` görünümünde kalır. Aynı UID ve SEQUENCE kayıtlarında `source_received_at` freshness tie-break olarak kullanılır; recurring ICS serilerinde occurrence kimliği `UID + RECURRENCE-ID` olur. UID olmayan lifecycle iletilerinde Gmail `thread_id`, canonical gönderen e-postası ve konu ailesi tarih/saat bağlamıyla eşleştirilir; tarih-only iptaller aynı günün zamanlı davetlerini bastırır.

### Meeting modeli

| Alan |
|---|
| `uid` |
| `title` |
| `organizer` |
| `thread_id` |
| `start_at` |
| `end_at` |
| `timezone` |
| `location` |
| `join_url` |
| `status` |
| `sequence` |
| `source_message_id` |
| `confidence` |
| `source_received_at` |
| `supersedes_start_at` |
| `recurrence_id` |

`STATUS:CANCELLED` veya `METHOD:CANCEL` olan ICS etkinlikleri listeye alınmaz. Aynı `UID` için daha yüksek `SEQUENCE` değerine sahip kayıt geçerli kabul edilir; böylece tarih değişikliği ve iptal mailleri eski daveti bastırır. Semantic metinde `iptal`, `ertelendi/rescheduled` ve `tentative` durumları da sınıflandırılır. `DTSTART;TZID=...`, UTC (`Z`) ve tarih-only ICS değerleri desteklenir; UTC zamanları Telegram özeti için Europe/Istanbul saatine çevrilir.

Yıl belirtilmeyen `5 Ocak` gibi tarihler önce hedef tarihin yılıyla oluşturulur. Bu aday hedef tarihten en az 60 gün gerideyse bir sonraki yıl adayı kullanılır; böylece yıl geçişinde gelecek toplantılar elenmezken, yakın geçmişteki tarihler yanlışlıkla ileri taşınmaz.

Haftanın günü ifadeleri mesajın alındığı tarihe göre çözülür. `Cuma` ve `bu/this Cuma` bir sonraki uygun Cuma’yı, `önümüzdeki/next Friday` ise aynı günse takip eden haftadaki ilk Cuma’yı seçer.

AppleScript JSON üretmez; güvenli `__MAIL_DIGEST_FIELD__` ayırıcısını kullanır. Temizleme ve ayrıştırma Python tarafında yapılır. Böylece e-posta içindeki tırnak, ters bölü ve kontrol karakterleri Telegram çıktısını bozmaz.

Transport alanları Python tarafında ASCII kontrol karakterlerinden ve sıfır genişlikli boşluklardan arındırılıp normalize edilir. ICS ham kaynağı ayrı taşınır; satır katlama bilgisi korunur ve calendar içeriği tek satıra indirgenmez.

## Kurulum ve yerel kullanım

Desteklenen bir Python 3.11 veya daha yeni sürüm kullanın.

Telegram bilgileri `~/.hermes_local_automation/telegram.env` dosyasında tutulur:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
# Required for a group/supergroup; optional user restriction for a private chat.
TELEGRAM_ALLOWED_USER_ID=your_user_id
```

Listener, configured group veya supergroup için `TELEGRAM_ALLOWED_USER_ID` yoksa
komutları fail-closed reddeder. Böylece gruptaki başka bir üye, özellikle
Company Reporting komutlarını yanlışlıkla çalıştıramaz.

Bu dosya yalnızca sahibi tarafından okunabilir olmalıdır:

```bash
chmod 600 ~/.hermes_local_automation/telegram.env
```

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python main.py --dry-run
```

`requirements.txt` doğrudan bağımlılıkları, `requirements.lock` ise aynı çalışma
ortamının transitive runtime bağımlılıklarını exact sürümlerle taşır; CI lock
dosyasını kurup onun tamamını audit eder.

Apple Mail yalnızca yerel rollback çalıştıracaksanız açık olmalı ve terminale Mail otomasyon izni verilmelidir. `TELEGRAM_ENV_FILE` ile kimlik bilgisi dosyası değiştirilebilir. İsteğe bağlı Company Reporting komutları, aynı Python ortamında kurulu bir paket/adapter üzerinden yüklenir; listener başka bir checkout'u `sys.path` ile çalışma zamanında enjekte etmez. Bağımsız listener kullanılıyorsa işlenmiş Telegram update kimlikleri varsayılan olarak `~/.hermes_local_automation/telegram/update_state.json` dosyasında kalıcı tutulur; `TELEGRAM_UPDATE_STATE_FILE` ile değiştirilebilir.

## Yapılandırma

Uygulama ayarları aşağıdaki ortam değişkenlerinden veya Telegram kimlik bilgileri için belirtilen `telegram.env` dosyasından okunur. Varsayılan dosya yolları kullanıcı ana dizini altında tutulur.

| Değişken | Kullanım ve varsayılan |
|---|---|
| `MAIL_SOURCE` | Mail kaynağı; varsayılan `gmail`, Apple Mail için açıkça `apple_mail`. |
| `MAIL_DIGEST_LOCK_FILE` | Ortak digest kilidi; varsayılan `/tmp/mail_unread_digest.lock`. |
| `MAIL_DIGEST_PARSE_CACHE_FILE` | Parse cache SQLite yolu; varsayılan `~/.hermes_local_automation/mail_digest/parse_cache.sqlite3`. |
| `MAIL_DIGEST_STATUS_FILE` | Çalışma durumu JSON yolu; varsayılan `~/.hermes_local_automation/mail_digest/status.json`. |
| `GMAIL_CREDENTIALS_FILE` | Gmail OAuth client dosyası; varsayılan `~/.hermes_local_automation/gmail/credentials.json`. |
| `GMAIL_TOKEN_FILE` | Gmail token JSON'u; varsayılan `~/.hermes_local_automation/gmail/token.json`. |
| `GMAIL_CACHE_FILE` | Gmail mesaj cache'i; varsayılan `~/.hermes_local_automation/gmail/cache.sqlite3`. |
| `GMAIL_BATCH_SIZE` | Full-sync ve Gmail API batch boyutu; varsayılan `10`, izin verilen aralık `1–50`. |
| `GMAIL_BATCH_PAUSE_SECONDS` | Ardışık Gmail batch'leri arasındaki bekleme; varsayılan `1.0` saniye. |
| `MEETING_REMINDER_STATE_FILE` | Hatırlatma deduplication/cursor state'i; varsayılan `~/.hermes_local_automation/mail_digest/reminders.json`. |
| `MEETING_REMINDER_MINUTES` | Toplantıdan kaç dakika önce hatırlatma gönderileceği; varsayılan `15`. |
| `MEETING_REMINDER_WINDOW_MINUTES` | Her hatırlatma taramasının zaman penceresi; varsayılan `5`. |
| `TELEGRAM_ENV_FILE` | Telegram sırlarının bulunduğu dosya; varsayılan `~/.hermes_local_automation/telegram.env`. |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token'ı; yalnızca `TELEGRAM_ENV_FILE` içinden okunur. |
| `TELEGRAM_CHAT_ID` | Hedef Telegram sohbeti; yalnızca `TELEGRAM_ENV_FILE` içinden okunur. |
| `TELEGRAM_ALLOWED_USER_ID` | Grup/süpergrup komutlarını kullanabilecek kullanıcı; grup için zorunludur. |
| `TELEGRAM_UPDATE_STATE_FILE` | Polling update kimlik state'i; varsayılan `~/.hermes_local_automation/telegram/update_state.json`. |
| `LISTENER_CONFLICT_BACKOFF_SECONDS` | Telegram getUpdates 409 çakışmasından sonra bekleme; varsayılan `300` saniye. Test ve kontrollü kullanımda `0` verilebilir. |

Telegram token'ını veya gerçek sohbet/kullanıcı kimliklerini kaynak koda, loglara ya da fixture'lara yazmayın.

## Gmail API kurulumu ve production kullanımı

Gmail kaynağı yalnızca `https://www.googleapis.com/auth/gmail.readonly` OAuth kapsamını ister. Kod mesajları değiştirmez, etiketlemez, okundu durumunu değiştirmez, taşımaz, silmez veya göndermez. Gmail hatasında Apple Mail'e otomatik fallback yoktur; seçilen kaynak başarısızsa digest de başarısız olur.

1. Google Cloud projesinde Gmail API'yi etkinleştirin ve Desktop application OAuth istemcisi oluşturun. OAuth doğrulamasının tamamlandığı varsayılmaz; bu operatör adımıdır.
2. İndirilen istemci dosyasını `~/.hermes_local_automation/gmail/credentials.json` konumuna koyun ve `chmod 600` uygulayın.
3. Bağımlılıkları `requirements.lock` üzerinden kurduktan sonra bir kez interaktif yetkilendirme çalıştırın:

   ```bash
   .venv/bin/python scripts/gmail_auth.py
   ```

   Normal `main.py` çalışması hiçbir zaman tarayıcı açmaz. Yenilenen token, aynı dizinde
   `0600` izinli geçici dosyaya yazılır, diske eşitlenir ve atomik olarak değiştirilir.
   `RefreshError`/`invalid_grant` yeniden yetkilendirme gerektirir; `TransportError`,
   socket veya timeout hataları geçicidir ve yeniden deneme önerir. Hata mesajları
   sağlayıcı yanıtını ya da hassas metni içermez.

4. İlk canlı kontrolü production job'ını değiştirmeden yapın:

   ```bash
   MAIL_SOURCE=gmail .venv/bin/python main.py --upcoming --dry-run
   ```

Varsayılan yollar `~/.hermes_local_automation/gmail/token.json` ve `~/.hermes_local_automation/gmail/cache.sqlite3`'tür. Sırasıyla `GMAIL_CREDENTIALS_FILE`, `GMAIL_TOKEN_FILE` ve `GMAIL_CACHE_FILE` ile değiştirilebilir. Bu credential, token ve cache dosyalarını repoya koymayın.

İlk Gmail çalışması, `getProfile` history sınırını snapshot'tan önce alır; 31 günlük normal mesaj ve 367 günlük ICS sunucu sorgularını retention sınırlarıyla daraltır, staging snapshot oluşturur ve cache/checkpoint/`retention_version=2` değerini aynı SQLite transaction'ında etkinleştirir. Full sync snapshot mesajlarını varsayılan 10'luk Gmail batch istekleriyle alır; `GMAIL_BATCH_SIZE` değeri 1–50 arasında ayarlanabilir ve ardışık batch'ler varsayılan olarak 1 saniye bekler (`GMAIL_BATCH_PAUSE_SECONDS`). Tekil 404'ler silinmiş mesaj sayılır. HTTP 429 ile 403 `rateLimitExceeded`/`userRateLimitExceeded` yanıtları en fazla altı exponential-backoff denemesiyle yeniden denenir ve varsa `Retry-After` süresine uyulur; rate-limit alan batch öğeleri bekleme sonrasında küçülen batch'lerle yeniden gönderilir. Diğer 5xx/ağ hatalarında mevcut üç denemeli retry kuralı korunur. Operasyon logları HTTP durumu ve Google reason'ını veya ağ istisnasının türünü içerir; URL, mesaj kimliği ve hata gövdesi loglanmaz. Checkpoint'i olan eski veya retention sürümü eksik cache için full-sync geçişi altı saatten sık denenmez (`retention_upgrade_last_attempt_ms`). Geçiş full sync'i başarısız olursa hata türü ve güvenli API hata mesajı loglanır, mevcut checkpoint ile incremental sync sürdürülür ve `retention_version` yükseltilmez; sonraki geçiş denemesi en erken altı saat sonra yapılır. Sonraki çalışmalar yalnızca `messageAdded`, `messageDeleted`, `labelAdded` ve `labelRemoved` türündeki history değişikliklerini uzlaştırır; API çağrısında label filtresi kullanılmaz. Inbox dışındaki yeni ve cache'te bulunmayan mesajlar getirilmez, silinmiş mesajlar yerel cache'ten Gmail `get` çağrısı yapılmadan çıkarılır. Eski history checkpoint'i HTTP 404 verirse güvenli tam sync yeniden başlar. Mesaj içeriği bozuksa yapısal kimlik/INBOX/tarih bilgisi geçerli olduğu sürece boş içerikli degraded kayıt saklanır.

Production transport Gmail'dir: `MAIL_SOURCE=gmail` kullanın. Gmail hatasında Apple Mail'e otomatik fallback yoktur; seçilen kaynak başarısızsa digest başarısız olur. launchd dosyalarını Gmail ayarlarıyla preview olarak üretmek için:

```bash
.venv/bin/python scripts/install_launchd.py --mail-source gmail
```

Komut günlük özet ile 5 dakikada bir çalışan hatırlatma görevini birlikte önizler. Hatırlatma süresini örneğin 30 dakikaya çıkarmak için `--reminder-minutes 30` kullanın. `--install` yalnızca plist dosyalarını yazar; servisleri yüklemez veya yeniden başlatmaz. Hatırlatma modu, gönderilecek toplantı yoksa Telegram'a mesaj göndermez. Başarılı gönderimlerin hash kimlikleri ve son başarılı reminder taraması varsayılan olarak `~/.hermes_local_automation/mail_digest/reminders.json` altında saklanır; başlık ve gönderen gibi özel bilgiler bu dosyaya düz metin olarak yazılmaz.

Gmail MIME gövdeleri bounded policy ile işlenir (`MAX_BODY_BYTES=256 KiB`, `MAX_RAW_MIME_BYTES=1 MiB`, `MAX_ICS_BYTES=256 KiB`). Cache'e full raw MIME yerine yalnızca ilgili calendar/ICS payload'ı yazılır; semantic fallback için gerekli gövde de aynı body sınırıyla tutulur. Birden fazla calendar payload'ında toplam sınır yalnızca bütünü sığan `VCALENDAR` bloklarını kabul eder; geçerli ICS byte ortasından kesilmez.

Semantic body-only toplantılarda tarih ve saat birlikte aranır. Bunun istisnası,
aynı tarih bağlamında açık toplantı ve iptal ifadesi bulunan date-only semantic
cancellation'dır: eski davetin saati korunarak lifecycle eşleştirmesine bırakılır.
Türkçe ay ve göreli tarih adları büyük/küçük harf duyarsızdır; ay adlarının ASCII
varyantları da tanınır. Bir kaydın parser hatası diğer kayıtları durdurmaz; digest
yalnızca hata sınıfı sayaçlarını loglar.
ICS motoru recurring master davetlerini yalnızca istenen aralık için açar; günlük,
haftalık ve gelecek görünümleri kendi aralıklarıyla sınırlıdır. Seri ana iptali
eşit veya daha yüksek `SEQUENCE` ile tüm occurrence'ları bastırır;
`RECURRENCE-ID` taşıyan tekil iptal yalnızca ilgili occurrence'ı kaldırır.

Komut varsayılan olarak yalnızca preview üretir; gerçek production job bu repository değişikliğiyle Gmail'e çevrilmez. Çok parçalı Telegram gönderiminde tüm parçalar başarılı olmadan reminder state ilerletilmez; Telegram'ın çoklu mesaj transaction'ı olmadığı için başarısız kısmi gönderimin daha sonraki denemede önceki parçaları tekrarlaması mümkündür.

Rollback gerektiğinde production job'larını açıkça `MAIL_SOURCE=apple_mail` ile yeniden üretip doğrulayın. Apple Mail rollback'inin bilinen performans sınırlaması vardır: nötr konulu toplantıları kaçırmamak için 30 günlük penceredeki her mesajın gövdesini okumaya çalışır; geçmiş yerel doğrulamalarda bu yol 180 saniyeyi aşmıştır. Rollback geçici bir işletim prosedürüdür; Apple Mail Issue-2 davranışı Gmail yolunun yerine kalıcı production kaynağı değildir.

`gmail.readonly` restricted bir kapsamdır. External OAuth projesi Testing durumunda bırakılırsa bu tür kapsamlar için refresh token'ları 7 günle sınırlı olabilir; uzun süreli unattended kullanım öncesinde Google Auth Platform production yapılandırması operatör tarafından tamamlanmalıdır.

## Çalıştırma modeli

- GitHub `main` dalı kanonik kaynaktır.
- Mac Studio ve MacBook Pro geliştirme içindir.
- Mac Mini tek runtime/production makinesidir.
- `telegram_listener.py`, bu botun tek Telegram polling ve komut sahibidir. Aynı token için başka bir polling süreci veya webhook çalıştırmayın; deploy betiği launchd içinde Hermes işi görürse durur.
- 08:00 launchd çalışması ile Telegram komutu aynı `run_digest()` akışını kullanır. `/tmp/mail_unread_digest.lock` dosyası yalnızca kilit buluşma noktasıdır; gerçek kilit `fcntl.flock()` ile tutulur ve process kapanınca kernel tarafından bırakılır. CLI/launchd varsayılan olarak hemen döner; bağımsız Telegram listener aynı kilidi 30 saniyeye kadar 0,5 saniyelik aralıklarla bekler. Dosyanın diskte kalması stale-lock oluşturmaz.
- CLI ve listener başlangıcında yalnızca o sürecin launchd logları döndürülür; iki süreç diğerinin loglarına dokunmaz ve dosya sistemi hataları digest akışını durdurmaz. Listener launchd işi `ThrottleInterval=30` ile çökme döngülerini sınırlar.
- Üç launchd plist şablonu `PYTHONUNBUFFERED=1` ayarlar; ayrıca listener'daki her `print()` `flush=True` kullanır. Başlangıç, komut ve hata çıktıları böylece tamponda beklemeden launchd loglarına yazılır.
- Beş dakikalık hatırlatma görevi de aynı `run_digest()` ve `flock` akışını kullanır; günlük özet veya Telegram komutuyla çakışırsa güvenli biçimde o turu atlar.
- Hatırlatma taraması `(son başarılı tarama, şimdi + lead time]` aralığını yakalar; ilk taramada küçük bir kurtarma örtüşmesi kullanılır. State cursor'ı yalnızca tarama ve gönderim başarılı olunca ilerler.
- Kilit yolunu farklı bir makine veya çalışma ortamı için `MAIL_DIGEST_LOCK_FILE` ile değiştirebilirsiniz.
- Mac Mini için bu depodaki `scripts/deploy_mac_mini.sh` kullanılır. Varsayılanı kontrol ve önizleme yapar; summary, reminder ve listener plist'lerini yazma ile launchd `bootout`/`bootstrap` işlemleri yalnızca `--apply` verildiğinde yapılır. `--apply` bu kod değişikliği sırasında çalıştırılmaz.
- Deploy ön kontrolü `.venv/bin/python` ve `python -m pip` işleyişini doğrular; bozuk venv için elle yeniden oluşturma komutunu bildirir ve venv'i otomatik değiştirmez. `ruff` ve `pyright`, yalnızca kendi `--version` kontrolleri başarılıysa kullanılır.
- Digest logları mesaj sayısı ve kaynak adını gösterebilir; mailbox adresi veya mesaj içeriği loglanmaz.

## Test

```bash
source .venv/bin/activate
./scripts/check.sh
```

Betik `PYTHON` değişkeniyle farklı bir Python yorumlayıcısı alabilir ve `ruff` ya da `pyright` bulunmadığında venv'i etkinleştirme yönergesi verir.

İsteğe bağlı runtime bağımlılık denetimi yalnızca açıkça istendiğinde çalıştırılır: `./scripts/check.sh --audit`. `bandit` kuruluysa yerel betik onu CI ile aynı `-s B108,B608` istisnalarıyla çalıştırır. GitHub Actions `push` ve `pull_request` üzerinde otomatik çalışmaz; yalnızca `workflow_dispatch` ile elle tetiklenir.

Test kapsamı parser matrisi, ICS/deduplication regresyonları, anonim Mail fixture'ları ve dış sistem hata akışlarını içerir. `tests/fixtures/` altında kişisel veri içermeyen 20 örnek bulunur; table-driven parser senaryoları, AppleScript timeout/bozuk çıktı, Telegram HTTP veya `ok:false` cevabı, 10.30/09.30/14.30 saatleri, tarih biçimleri, yıl geçişi, hafta günleri, quoted reply, iptal, erteleme ve çoklu tarih senaryoları test edilir. Deploy betiği testleri geçici bir Git deposu ve sahte `git`, `launchctl` ve Python araçları kullanır; gerçek makine servislerine dokunmaz.

Noktalı tarih/saat bağlamı her token için yalnızca yakındaki metin penceresinde aranır. `tests/test_parser_performance.py`, 100 anonim sentetik mesajın günlük özetini ve sabit toplantı sayısını doğrular. `python scripts/bench_parser.py --cache` daily, weekly, upcoming ve reminder sürelerini önbelleksiz, soğuk ve sıcak önbellek koşullarında ölçer; yalnızca geçici cache dosyaları kullanır.

Ayrıştırma çıktısını etkileyen her değişiklikte `mail_digest/parsing/__init__.py` içindeki `PARSER_VERSION` artırılır; böylece eski parse-cache kayıtları kullanılmaz. Cache girdileri her çalışmada, başlangıç aralığı bugünden iki günden daha eski olanlar için temizlenir.

Mail erişimi ve Telegram gönderimi salt-okunur operasyon mantığıyla tasarlanmıştır: mesaj silme, taşıma, işaretleme, cevaplama, yönlendirme veya taslak oluşturma yapılmaz.
