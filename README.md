# Voice-generals

پک صدای **طنز و رکیک** برای یونیت‌های *Command & Conquer: Generals — Zero Hour*.

پک با **همان نام فایل‌های اصلی بازی** ساخته می‌شود، پس بدون دست‌زدن به هیچ `.ini` نصب می‌شود
(فایل‌های loose در `Data\Audio\Sounds` اول از همه خوانده می‌شوند، قبل از `AudioEnglish.big`).

| مورد | مقدار |
|---|---|
| بازی | Generals: Zero Hour (نه Generals 1) |
| زبان | فارسی محاوره‌ای + فینگلیش |
| فرمت خروجی | WAV، مونو، 22050Hz، 16bit PCM |
| مسیر نصب | `<game>/Data/Audio/Sounds/` یا `.big` با سوییچ `-mod` |
| وضعیت | بولدوزر آمریکا ✅ — بقیهٔ یونیت‌ها در نوبت |

## چرا نام فایل‌ها مهم است

موتور بازی صدا را این‌طور پیدا می‌کند:

1. `Data\Audio\Sounds\<name>.wav` (لوکال فایل‌سیستم — **اولویت اول**)
2. آرشیوهای `*.big` (`AudioEnglish.big`, `AudioEnglishZH.big`)
3. اگر فایل لوکال نبود، از BIG خوانده می‌شود

پس کافی است فایل‌ها را با **دقیقاً همان اسم** بگذاری. اسم‌ها از `Voice.ini` و
`SoundEffects.ini` خود بازی استخراج می‌شوند (`data/voice_events.json`).

## نکته‌ای که اکثر پک‌ها اشتباه می‌کنند

بعضی یونیت‌ها در ژنرال‌های خاص صدا/رویداد **جدا** دارند و بعضی ندارند. مثلاً:

* بولدوزر آمریکا در **هر چهار ژنرال** (پایه، لیزر، هوایی، سوپر سلاح) همان
  `DozerUSAVoice*` را استفاده می‌کند → یک پک برای همه.
* ولی `Pathfinder` (پیاده‌نظام)، `KingRaptor` (هوایی)، `LaserCrusader` و `SuperHacker`
  رویداد صدای مخصوص خودشان را دارند و باید جدا ساخته شوند.
* `Boss_VehicleDozer` (باس جنرال) بولدوزرِ **چینی** است، نه آمریکایی.

نگاشت کامل «هر آبجکت → هر اسلات صوتی → هر فایل wav» در `data/object_voices.json` است
(۶۴۰ آبجکت، ۱۴۰۹ رویداد صوتی). قبل از ساخت هر یونیت، `verify` کم‌وکاست را چک می‌کند.

## ساختار

```
data/packs/*.json       متن پک (هر یونیت، هر اسلات، هر فایل wav)
data/voice_events.json  رویدادهای صوتی اصلی + لیست فایل‌ها
data/object_voices.json آبجکت‌ها + اسلات‌های صوتی‌شان
tools/fetch_vanilla.py  گرفتن INIهای اصلی بازی (کش در .cache، کامیت نمی‌شود)
tools/build_data.py     تبدیل INI به JSON
tools/build_pack.py     ساخت پک: تمیزکاری صدا + چک پوشش + انتشار + .big
tools/big.py            خواندن/نوشتن آرشیو BIGF (جایگزین FinalBIG)
tools/preview.py        ساخت صفحهٔ پخش HTML برای گوش‌دادن به کل پک
docs/INSTALL.md         راهنمای نصب
docs/SOURCING.md        مستند صحت نگاشت صداها
```

## استفاده

```bash
python3 tools/fetch_vanilla.py                 # INIهای اصلی را بگیر (یک‌بار)
python3 tools/build_data.py                    # دیتابیس JSON
python3 tools/build_pack.py data/packs/usa_dozer.json --list   # چک پلن
python3 tools/build_pack.py data/packs/usa_dozer.json --big    # ساخت پک + آرشیو
python3 tools/preview.py usa_dozer             # صفحهٔ گوش‌دادن
```

`build_pack.py` تک‌تک فایل‌ها را چک می‌کند: هر فایل در رویداد اصلی موجود باشد و
هیچ فایلی از قلم نیفتد (بولدوزر = ۴۱ از ۴۱ فایل در ۹ رویداد).

## نصب در بازی

راه ۱ (ساده): پوشهٔ `build/usa_dozer/mod/` را روی ریشهٔ بازی کپی کن، طوری که بشود:

```
Command & Conquer Generals - Zero Hour/Data/Audio/Sounds/vdousea.wav
```

راه ۲ (تمیز، بدون دست‌زدن به فایل‌های بازی):

```
GeneralsZH.exe -mod "C:\path\to\usa_dozer.big"
```

برای برگشت به صداهای اصلی، فقط فایل‌های `vdou*.wav` را از `Data/Audio/Sounds` پاک کن.

## محدودهٔ محتوا

فحش ناموسی، فینگلیش، کنایهٔ سیاسی و دست‌انداختن به تیکه‌کلام ژنرال‌ها. عمداً **بدون**
توهین قومی/مذهبی؛ هدف خندیدن به کلیشه‌های خود بازی است، نه به یک گروه واقعی.

## لایسنس

کد ابزارها: MIT. صداهای اصلی بازی مالِ EA است و در این ریپو کامیت نمی‌شوند؛
فقط متن پک (`data/packs`) و ابزارها اینجا نگه داشته می‌شوند.
