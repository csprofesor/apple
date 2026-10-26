# PlayRepo Ekosistemi 🎬

PlayRepo; Nuvio/Stremio için hızlı akış sağlayan eklenti, otomatik web kazıma/çözümleme yapan macOS masaüstü uygulaması ve Android uygulamasından oluşan eksiksiz bir medya otomasyon ekosistemidir.

---

## 📑 İçindekiler
1. [Nuvio Eklentisi Kurulumu](#1-nuvio-eklentisi-kurulumu)
2. [macOS Uygulaması Kurulumu ve Kullanımı](#2-macos-uygulaması-kurulumu-ve-kullanımı)
3. [Android APK Derleme ve Kurulumu](#3-android-apk-derleme-ve-kurulumu)
4. [Kategori Mimarisi ve Ayrık Küme Kuralı](#4-kategori-mimarisi-ve-ayrık-küme-kuralı)
5. [Sunucu ve Yayın Özellikleri](#5-sunucu-ve-yayın-özellikleri)

---

## 1. Nuvio Eklentisi Kurulumu

Nuvio ve Stremio uygulamalarında eklentiyi kurmak için aşağıdaki manifest URL adresini kullanın:

```text
https://raw.githubusercontent.com/csprofesor/apple/main/playrepo/manifest.json
```

### Kurulum Adımları:
1. **Nuvio** uygulamasını açın.
2. **Eklentiler (Addons / Repositories)** sekmesine geçin.
3. **+ (URL ile Ekle)** alanına yukarıdaki manifest linkini yapıştırıp onaylayın.
4. Eklenti anında aktifleşir ve ana sayfanızda platform rafları görünür.

---

## 2. macOS Uygulaması Kurulumu ve Kullanımı

macOS uygulaması yerel web arayüzü (`http://localhost:5678`) üzerinden film ve dizi tarama işlemlerini yönetir.

### Başlatma Seçenekleri:
* **Seçenek A (Hazır Uygulama):**
  `macos/PlayRepo.app` paketine çift tıklayarak başlatın.
* **Seçenek B (Terminalden Çalıştırma):**
  ```bash
  cd macos
  python3 playrepo_mac.py
  ```

### Temel Özellikler:
* **Canlı Dashboard:** Otomatik olarak tarayıcınızda `http://localhost:5678` adresini açar.
* **GitHub Senkronizasyonu:** Ayarlar sekmesine `repo` yetkisine sahip GitHub Kişisel Erişim Belirtecinizi (Token) girerek doğrudan deponuza otomatik yükleme yapabilirsiniz.
* **Anında Tek Tek Yükleme:** Çözülen her film anında depoya commit edilir; kapatmak istediğinizde tüm taramanın bitmesini beklemeniz gerekmez.
* **Güvenli Sıfırlama (Tümünü Sıfırla):** Depodaki tüm film ve katalogları temizlerken arka plandaki kazıma motorunu (`ENGINE_INSTANCE.stop()`) anında kapatır, arkadan yeni film eklenmesini engeller.

---

## 3. Android APK Derleme ve Kurulumu

Android uygulamasını kendi cihazınızda çalıştırmak veya APK üretmek için:

### APK Derleme:
```bash
./gradlew assembleDebug
```
Derlenen APK dosyası şu dizinde oluşturulur:
```text
app/build/outputs/apk/debug/app-debug.apk
```

### Cihaza Kurulum:
```bash
adb install app/build/outputs/apk/debug/app-debug.apk
```

---

## 4. Kategori Mimarisi ve Ayrık Küme Kuralı

Kategoriler tekil ve müstakil isimlerle yapılandırılmıştır:

### Dijital Platformlar:
* **Netflix**, **Disney+**, **Prime Video**, **Apple TV+**, **HBO Max**, **Hulu**, **Mubi**, **BluTV**

### Film Türleri:
* **Belgesel**, **Bilim Kurgu**, **Fantastik**, **Animasyon**, **Korku**, **Gerilim**, **Gizem**, **Suç**, **Aksiyon**, **Macera**, **Komedi**, **Romantik**, **Tarih**, **Savaş**, **Aile**, **Dram**

> ⚠️ **Ayrık Küme (Tekil Atama) Prensibi:**  
> Bir kategoriye (örneğin Netflix veya Aksiyon) atanan bir film, sonraki hiçbir kategoriye **kesinlikle tekrar eklenmez**. Böylece her film yalnızca tek bir rafta yer alır.

---

## 5. Sunucu ve Yayın Özellikleri

* **Net ve Anlaşılır Sunucu Başlıkları:**
  - `Play • Dublaj 1 • 1080p • [TR Dublaj]`
  - `Play • Dublaj 2 • 1080p • [TR Dublaj]`
  - `Play • Altyazı 1 • 1080p • [TR Altyazılı]`
  - `Play • Sunucu 1 • 1080p • [TR]`
* **Takılma / Buffering Önleme:**
  Tüm akışlarda HLS (`.m3u8`) bağlantıları ilk sıraya alınır; doğru `Referer` ve `User-Agent` proxy başlıkları eklenerek player takılmaları engellenir.
