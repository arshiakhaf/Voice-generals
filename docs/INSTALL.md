# نصب پک

## پیش‌نیاز

* Command & Conquer: Generals — **Zero Hour**
* نسخه‌ای که سوییچ `-mod` را پشتیبانی کند (Steam / EA App / TheSuperHackers build)

## راه ۱ — فایل‌های loose (ساده‌ترین)

1. پوشهٔ `build/<pack>/mod/` را باز کن. داخلش این مسیر است:

   ```
   Data/Audio/Sounds/vdousea.wav
   ```

2. همین پوشهٔ `Data` را روی ریشهٔ نصب بازی کپی کن، مثلاً:

   ```
   C:\Program Files (x86)\Steam\steamapps\common\Command & Conquer Generals - Zero Hour\
   ```

3. بازی را باز کن. بولدوزر آمریکا (و کلن‌های سازنده در هر چهار ژنرال آمریکا) صدای جدید دارد.

**برگشت به قبل:** فقط فایل‌های `vdou*.wav` را از `Data/Audio/Sounds` پاک کن.

## راه ۲ — آرشیو `.big` با سوییچ `-mod` (تمیزترین)

1. فایل `build/<pack>.big` را جایی بگذار.
2. شورتکات بازی را با آرگومان اجرا کن:

   ```
   GeneralsZH.exe -mod "C:\packs\usa_dozer.big"
   ```

   آرشیو بعد از آرشیوهای اصلی mount می‌شود، پس فایل داخلش اولویت دارد.

## چرا بدون `-mod` هم کار می‌کند؟

`AudioEventRTS::generateFilenamePrefix()` مسیر
`Data\Audio\Sounds\<lang>\` و بعد `Data\Audio\Sounds\` را می‌سازد و از طریق
`FileSystem::openFile()` اول **لوکال فایل‌سیستم** را نگاه می‌کند. یعنی هر فایلی که
در پوشهٔ بازی باشد، برندهٔ `AudioEnglish.big` است.

## نکاتی که باعث می‌شود صدا نشنوی

| مشکل | علت |
|---|---|
| صدا عوض نشد | اسم فایل اشتباه است (حساس به اسم، نه حرف بزرگ/کوچک) |
| فقط بعضی خط‌ها عوض شد | آن رویداد فایل‌های بیشتری دارد؛ `python3 tools/build_pack.py ... --list` را ببین |
| یونیت دیگر صدا ندارد | `VoiceDisarm`/`VoiceRepair` فقط برای بولدوزر صدا دارند؛ برای بقیه اسلات تعریف نشده |
| صدای قدیمی می‌آید | کش صدا؛ بازی را کامل ببند و دوباره باز کن |
| در بازی آنلاین | پک‌ها باید برای همه یکسان باشند، وگرنه ناهماهنگی می‌شود |

## بررسی نصب

```bash
python3 tools/build_pack.py data/packs/usa_dozer.json --list
```

باید بنویسد `files: 41` و هیچ `PROBLEMS` نداشته باشد.
