# Audio Watermarking (MP3) — DWT + QIM (Python)

Bu proje, MP3 dosyasinin **ses icerigine** (dosya adi veya ID3 etiketlerine degil) **gorunmez ve dayanıklı** bir watermark gomuyor.  
Dosya adi degisse ya da metadata silinse bile watermark, ses iceriginden geri alinabilir.

Akis: MP3 -> PCM (WAV) -> DWT (Discrete Wavelet Transform) alaninda QIM (Quantization Index Modulation) ile gomme -> tekrar MP3.

---

## Gereksinimler

- **Python 3.9+**
- **FFmpeg** (terminalden calisabilir olmali)
- Python paketleri: `numpy`, `scipy`, `pywavelets`

---

## Proje Yapisi

Girdi MP3 dosyanizi ayni klasore koyun:

```
audio-watermark/
├─ app.py
└─ music.mp3
```

---

## Kurulum

### 1) Python paketleri

```bash
pip install numpy scipy pywavelets
```

### 2) FFmpeg kontrolu

```bash
ffmpeg -version
```

Versiyon bilgisi goruyorsaniz hazirsiniz.

---

## Hizli Baslangic

### Watermark Gom (watermarked.mp3 olustur)

`music_id` ve `copyright_id` degerlerini ses icerigine gomur:

```bash
python app.py embed --in music.mp3 --out watermarked.mp3 --music-id "MUSIC-001" --copyright-id "TELIF-2026" --strength 1.8 --repetition 9 --stride 5 --band d1
```

Beklenen sonuc:

- `watermarked.mp3` olusur.
- JSON cikisi `"status": "ok"` dondurur.

---

### Watermark Cikar (gomulu veriyi oku)

Gommede kullanilan **ayni parametreleri** kullanin:

```bash
python app.py extract --in watermarked.mp3 --strength 1.8 --repetition 9 --stride 5 --band d1
```

Beklenen cikti:

```json
{
  "status": "ok",
  "extracted": {
    "music_id": "MUSIC-001",
    "copyright_id": "TELIF-2026",
    "md5": "...",
    "v": 1
  }
}
```

---

## Parametre Notlari (Onemli)

Extract sirasinda su parametreler **gommedekiyle ayni** olmali:

- `--strength`
- `--repetition`
- `--stride`
- `--band` (`d1` veya `d2`)
- (degistirildi ise) `--wavelet`, `--level`

Guclu ve stabil ayarlar (onerilen):

- `--strength 1.8`
- `--repetition 9`
- `--stride 5`
- `--band d1`

---

## Sik Karsilasilan Sorunlar

### CRC verification failed

Ornek hata:

```
CRC verification failed
```

Genelde watermarkin guvenilir sekilde geri alinamadigini gosterir (MP3 yeniden kodlama etkileri veya parametre uyumsuzlugu).

Cozum:

- Extract tarafinda **ayni parametreleri** kullanin
- Dayanikliligi artirin:
  - `--repetition` degerini artirin (ornegin 9 -> 11)
  - `--strength` degerini biraz yukseltin (ornegin 1.8 -> 2.0)
  - `--stride` degerini azaltin (ornegin 5 -> 4)

---

## Notlar

Bu, kimliklendirme ve takip icin pratik bir watermark prototipidir.
Uretim ortami icin asagidakiler eklenebilir:

- Payload sifreleme (AES)
- Hata duzeltme kodlari (BCH/Reed-Solomon)
- Daha guclu dayanıklilik icin spread-spectrum gomleme
