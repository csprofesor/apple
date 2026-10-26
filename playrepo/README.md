# PlayRepo — Nuvio Eklentisi (Play v1.0.8)

Nuvio ve Stremio uyumlu, ultra hızlı, Türkçe dublaj ve altyazı destekli film ve dizi kaynak eklentisi.

---

## 🚀 Kurulum (Nuvio / Stremio)

Nuvio veya Stremio uygulamanıza tek tıkla veya URL ile ekleyebilirsiniz:

### 1. Manifest URL
```text
https://raw.githubusercontent.com/csprofesor/apple/main/playrepo/manifest.json
```

### 2. Kurulum Adımları
1. **Nuvio** uygulamasını açın.
2. Sol menüden **Eklentiler (Addons / Repositories)** sekmesine gidin.
3. Sağ üst köşedeki **+ (URL ile Ekle / Add by URL)** seçeneğini seçin.
4. Yukarıdaki Manifest URL adresini yapıştırın ve **Kur (Install)** butonuna basın.
5. Ana sayfaya döndüğünüzde yeni kategori rafları listelenecektir.

---

## ✨ Özellikler

### 1. Bağımsız Dijital Platform Rafları
Tüm platformlar birbirinden bağımsız tekil başlıklar halinde düzenlenmiştir:
- **Netflix**
- **Disney+**
- **Prime Video**
- **Apple TV+**
- **HBO Max**
- **Hulu**
- **Mubi**
- **BluTV**

### 2. Müstakil Film ve Dizi Türleri
Birleşik (ör. "Aksiyon & Macera" gibi) karmaşık türler kaldırılmış, her tür müstakil hale getirilmiştir:
- **Film Türleri:** Belgesel, Bilim Kurgu, Fantastik, Animasyon, Korku, Gerilim, Gizem, Suç, Aksiyon, Macera, Komedi, Romantik, Tarih, Savaş, Aile, Dram.
- **Dizi Türleri:** Popüler Diziler, Netflix Dizileri, Bilim Kurgu, Fantastik, Korku, Gerilim, Gizem, Suç, Aksiyon, Komedi, Dram.

### 3. Katı Ayrık Kategori Kuralı (Disjoint Shelf)
- Bir kategoriye giren film, **diğer hiçbir kategoride asla tekrar etmez**.
- Aynı film ana sayfada birden fazla rafı işgal etmez; her içerik sadece kendi ana türünde listelenir.

### 4. Standart ve Temiz Sunucu Başlıkları
Gereksiz çıplak "Play" etiketleri kaldırılmış ve anlaşılır standart format getirilmiştir:
- `Play • Dublaj 1 • 1080p • [TR Dublaj]`
- `Play • Dublaj 2 • 1080p • [TR Dublaj]`
- `Play • Altyazı 1 • 1080p • [TR Altyazılı]`
- `Play • Sunucu 1 • 1080p • [TR]`

### 5. Sıfır Buffering & Anında Başlatma
- Tüm HLS (`.m3u8`) akışları otomatik olarak en başa yerleştirilir.
- Oynatıcı için gerekli olan `Referer` ve `User-Agent` başlıkları önceden tanımlanmıştır.
