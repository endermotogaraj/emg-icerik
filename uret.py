"""
EMG Otomasyon - Görsel/Video üretici (GitHub Actions'ta çalışır)

Girdi: ortam değişkeni IS (JSON). Örnek:
{
  "id": "12",
  "format": "Karusel",              # Karusel | Tekli | Reels
  "urun_adi": "MOTUL 7100 10W40 4T",
  "urun_alt": "1 L · %100 Sentetik",
  "urun_foto": "https://.../motul.jpg",
  "sahne_istemi": "moody motorcycle garage ...",
  "hook": ["MOTORUNUN KALBİ", "BU YAĞLA ATIYOR!"],
  "slaytlar": [{"baslik": "...", "metin": "..."}],
  "site": "otomotodunyasi.com"
}
Çıktı: cikti/<id>/  →  1.jpg, 2.jpg, ... (+ reels.mp4) + sonuc.json
"""
import base64, io, json, os, subprocess, sys, textwrap, time, traceback
import numpy as np
import requests
import cv2
from PIL import Image, ImageDraw, ImageFont, ImageFilter

GOLD = (212, 162, 76)
WHITE = (255, 255, 255)
KOK = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(KOK, 'fontlar')


def buyuk(m):
    """Türkçe büyük harf: i→İ, ı→I"""
    return (m or '').replace('i', 'İ').replace('ı', 'I').upper()


# ------------------------------------------------------------------ fontlar
def font(tur, boyut):
    adaylar = {
        'baslik': ['Anton-Regular.ttf'],
        'govde': ['Oswald.ttf'],
    }[tur]
    for a in adaylar:
        p = os.path.join(FONT_DIR, a)
        if os.path.exists(p):
            f = ImageFont.truetype(p, boyut)
            if a == 'Oswald.ttf':
                try:
                    f.set_variation_by_name('Medium')
                except Exception:
                    pass
            return f
    yedek = '/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf'
    return ImageFont.truetype(yedek, boyut)


# ------------------------------------------------------------------ sahne
def sahne_uret(istem):
    yerel = os.environ.get('YEREL_SAHNE')
    if yerel:
        return Image.open(yerel).convert('RGB')
    hesap = os.environ['CF_ACCOUNT_ID']
    url = f'https://api.cloudflare.com/client/v4/accounts/{hesap}/ai/run/@cf/black-forest-labs/flux-1-schnell'
    tam = ('deserted empty scene with nobody in it, ' + istem + ', cinematic photography, dramatic warm amber light, shallow depth of field, '
           'empty space in the center foreground for a product, no text, no letters, no logos, no watermark, no humans, no person, unoccupied')
    son = ''
    for deneme in range(3):
        r = requests.post(url, headers={'Authorization': 'Bearer ' + os.environ['CF_API_TOKEN']},
                          json={'prompt': tam, 'steps': 6}, timeout=120)
        if r.status_code == 200 and r.json().get('result', {}).get('image'):
            return Image.open(io.BytesIO(base64.b64decode(r.json()['result']['image']))).convert('RGB')
        son = f'{r.status_code}: {r.text[:300]}'
        time.sleep(5 * (deneme + 1))
    raise RuntimeError('Cloudflare sahne üretemedi → ' + son)


def kapla(img, W, H, blur=2.2):
    oran = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * oran) + 1, int(img.height * oran) + 1), Image.LANCZOS)
    x = (img.width - W) // 2
    y = (img.height - H) // 2
    img = img.crop((x, y, x + W, y + H))
    return img.filter(ImageFilter.GaussianBlur(blur)) if blur else img


# ------------------------------------------------------------------ ürün
def urun_indir(kaynak):
    if os.path.exists(kaynak):
        return Image.open(kaynak).convert('RGBA')
    r = requests.get(kaynak, timeout=60, headers={'User-Agent': 'Mozilla/5.0'})
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert('RGBA')


def beyaz_zemin_sil(img):
    """Yedek yöntem: kenardan beyaz zemini doldurup siler."""
    src = cv2.cvtColor(np.array(img.convert('RGB')), cv2.COLOR_RGB2BGR)
    pad = cv2.copyMakeBorder(src, 6, 6, 6, 6, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    mask = np.zeros((pad.shape[0] + 2, pad.shape[1] + 2), np.uint8)
    cv2.floodFill(pad.copy(), mask, (0, 0), (0, 0, 0), (30,) * 3, (30,) * 3,
                  cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8) | 8)
    bg = mask[1:-1, 1:-1][6:-6, 6:-6]
    alpha = np.where(bg > 0, 0, 255).astype(np.uint8)
    cnts, _ = cv2.findContours(alpha, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if cnts:
        c = max(cnts, key=cv2.contourArea)
        alpha = np.zeros_like(alpha)
        cv2.drawContours(alpha, [c], -1, 255, -1)
    alpha = cv2.GaussianBlur(cv2.erode(alpha, np.ones((3, 3), np.uint8)), (3, 3), 0)
    rgba = np.array(img.convert('RGBA'))
    rgba[:, :, 3] = alpha
    return Image.fromarray(rgba)


def arka_plan_sil(img):
    # Zaten şeffafsa dokunma
    a = np.array(img)[:, :, 3]
    if (a < 250).mean() > 0.05:
        sonuc = img
    else:
        try:
            from rembg import remove, new_session
            sonuc = remove(img, session=new_session('isnet-general-use'))
        except Exception as e:
            print('rembg kullanılamadı, beyaz zemin yöntemi:', e)
            sonuc = beyaz_zemin_sil(img)
    # Kenarlara kırp
    bbox = sonuc.split()[3].point(lambda p: 255 if p > 20 else 0).getbbox()
    if bbox:
        sonuc = sonuc.crop(bbox)
    # Alttaki açık renkli zemin/yansıma şeridini kırp
    arr = np.array(sonuc)
    son = arr.shape[0]
    while son > arr.shape[0] * 0.85:
        satir = arr[son - 1]
        m = satir[:, 3] > 128
        if (not m.any()) or satir[m][:, :3].mean() > 170:
            son -= 1
        else:
            break
    return Image.fromarray(arr[:son])


# ------------------------------------------------------------------ çizim yardımcıları
def gradyan(W, H, ust=0.32, alt=0.70, ust_g=235, alt_g=245):
    g = Image.new('L', (1, H))
    for y in range(H):
        t = y / H
        v = 0
        if t < ust:
            v = int(ust_g * (1 - t / ust) ** 1.3)
        if t > alt:
            v = int(alt_g * ((t - alt) / (1 - alt)) ** 1.2)
        g.putpixel((0, y), v)
    g = g.resize((W, H))
    siyah = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    siyah.putalpha(g)
    return siyah


def urun_yerlestir(canvas, urun, merkez_x, alt_y, yukseklik, max_genislik=None):
    W, H = canvas.size
    oran = yukseklik / urun.height
    if max_genislik and urun.width * oran > max_genislik:
        oran = max_genislik / urun.width
    u = urun.resize((max(1, int(urun.width * oran)), max(1, int(urun.height * oran))), Image.LANCZOS)
    ux, uy = int(merkez_x - u.width / 2), int(alt_y - u.height)
    # turuncu hâle
    glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((ux - 170, uy + 60, ux + u.width + 170, uy + u.height + 40), fill=(255, 120, 20, 120))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)))
    # zemin gölgesi
    sh = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse((ux - 40, uy + u.height - 22, ux + u.width + 40, uy + u.height + 26), fill=(0, 0, 0, 200))
    canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(16)))
    # yansıma
    ref = u.transpose(Image.FLIP_TOP_BOTTOM)
    ra = ref.split()[3].point(lambda p: int(p * 0.22))
    fade = Image.linear_gradient('L').resize(ref.size).point(lambda p: 255 - p)
    ref.putalpha(Image.composite(ra, Image.new('L', ref.size, 0), fade))
    katman = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    katman.paste(ref, (ux, uy + u.height + 4), ref)
    canvas.alpha_composite(katman)
    katman = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    katman.paste(u, (ux, uy), u)
    canvas.alpha_composite(katman)


def sigdir(d, metin, tur, boyut, max_w, min_boyut=28):
    while boyut > min_boyut:
        f = font(tur, boyut)
        if d.textlength(metin, font=f) <= max_w:
            return f
        boyut -= 2
    return font(tur, min_boyut)


def ortala(d, W, metin, y, f, renk, golge=True):
    bb = d.textbbox((0, 0), metin, font=f)
    x = (W - (bb[2] - bb[0])) // 2 - bb[0]
    if golge:
        d.text((x + 3, y + 4), metin, font=f, fill=(0, 0, 0, 190))
    d.text((x, y), metin, font=f, fill=renk)
    return bb[3]


def satirlara_bol(d, metin, f, max_w):
    kelimeler, satirlar, s = metin.split(), [], ''
    for k in kelimeler:
        dene = (s + ' ' + k).strip()
        if d.textlength(dene, font=f) <= max_w:
            s = dene
        else:
            if s:
                satirlar.append(s)
            s = k
    if s:
        satirlar.append(s)
    return satirlar


def hook_ciz(d, W, satirlar, y, max_w, boyut=118):
    renkler = [WHITE, GOLD, WHITE, GOLD]
    for i, s in enumerate(satirlar[:3]):
        f = sigdir(d, buyuk(s), 'baslik', boyut, max_w)
        ortala(d, W, buyuk(s), y, f, renkler[i % 4])
        y += int(f.size * 1.08)
    return y


def cta_hap(d, W, y, site, boyut=44):
    f = font('baslik', boyut)
    bb = d.textbbox((0, 0), site, font=f)
    pw, ph = bb[2] - bb[0] + 100, boyut + 40
    px = (W - pw) // 2
    d.rounded_rectangle((px, y, px + pw, y + ph), radius=ph // 2, fill=GOLD)
    d.text((px + 50 - bb[0], y + (ph - (bb[3] - bb[1])) // 2 - bb[1]), site, font=f, fill=(18, 18, 18))
    return y + ph


def logo_koy(canvas, boy=150, kenar=36, konum='sag-alt'):
    adaylar = [os.path.join(KOK, 'EnderGarageLogo.png'), os.path.join(KOK, 'varliklar', 'logo.png')]
    p = next((a for a in adaylar if os.path.exists(a)), None)
    if not p:
        return
    logo = Image.open(p).convert('RGBA')
    logo = logo.resize((int(logo.width * boy / logo.height), boy), Image.LANCZOS)
    W, H = canvas.size
    if konum == 'sag-alt':
        canvas.alpha_composite(logo, (W - logo.width - kenar, H - boy - kenar))
    else:
        canvas.alpha_composite(logo, (W - logo.width - kenar, kenar))


def sayfa_no(d, W, no, toplam):
    if toplam < 2:
        return
    f = font('govde', 30)
    metin = f'{no}/{toplam}'
    d.rounded_rectangle((W - 120, 40, W - 40, 88), radius=24, fill=(0, 0, 0, 150))
    bb = d.textbbox((0, 0), metin, font=f)
    d.text((W - 80 - (bb[2] - bb[0]) // 2 - bb[0], 64 - (bb[3] - bb[1]) // 2 - bb[1]), metin, font=f, fill=WHITE)


# ------------------------------------------------------------------ slayt tipleri
def hook_satirlari(d, is_, W):
    h = is_.get('hook') or is_.get('urun_adi', '')
    if isinstance(h, str):
        h = [p.strip() for p in h.split('|')] if '|' in h else satirlara_bol(d, buyuk(h), font('baslik', 110), W - 120)
    return [s for s in h if s][:3]


def kapak(sahne, urun, is_, W=1080, H=1350, no=1, toplam=1):
    dikey = H > 1500
    k = 1.3 if dikey else 1.0
    c = kapla(sahne, W, H).convert('RGBA')
    c.alpha_composite(gradyan(W, H))
    urun_yerlestir(c, urun, W // 2, int(H * (0.70 if dikey else 0.685)),
                   int(H * (0.38 if dikey else 0.41)), max_genislik=int(W * 0.60))
    d = ImageDraw.Draw(c)
    hook_ciz(d, W, hook_satirlari(d, is_, W), int(H * (0.10 if dikey else 0.065)), W - 110,
             boyut=int(118 * k))
    y = int(H * (0.745 if dikey else 0.75))
    ad = buyuk(is_.get('urun_adi', ''))
    ortala(d, W, ad, y, sigdir(d, ad, 'baslik', int(46 * k), W - 160), WHITE)
    if is_.get('urun_alt'):
        ortala(d, W, is_['urun_alt'], y + int(58 * k), font('govde', int(32 * k)), (225, 225, 225))
    y2 = int(H * (0.835 if dikey else 0.845))
    cta_hap(d, W, y2, is_.get('site', 'otomotodunyasi.com'), boyut=int(44 * k))
    ortala(d, W, 'Hemen sipariş ver  »  link profilde', y2 + int(100 * k), font('govde', int(30 * k)), (230, 230, 230))
    sayfa_no(d, W, no, toplam)
    logo_koy(c, boy=int(H * (0.08 if dikey else 0.11)))
    return c.convert('RGB')


def bilgi(sahne, urun, is_, slayt, W=1080, H=1350, no=2, toplam=3):
    c = kapla(sahne, W, H, blur=9).convert('RGBA')
    c.alpha_composite(Image.new('RGBA', (W, H), (0, 0, 0, 120)))
    c.alpha_composite(gradyan(W, H, ust=0.60, alt=0.80, ust_g=230, alt_g=200))
    urun_yerlestir(c, urun, W // 2, int(H * 0.90), int(H * 0.42), max_genislik=int(W * 0.55))
    d = ImageDraw.Draw(c)
    # numara rozeti
    f_no = font('baslik', 54)
    rozet = f'{no - 1:02d}'
    d.rounded_rectangle((70, 80, 190, 170), radius=20, fill=GOLD)
    bb = d.textbbox((0, 0), rozet, font=f_no)
    d.text((130 - (bb[2] - bb[0]) // 2 - bb[0], 125 - (bb[3] - bb[1]) // 2 - bb[1]), rozet, font=f_no, fill=(18, 18, 18))
    # başlık
    y = 215
    f_b = font('baslik', 92)
    for s in satirlara_bol(d, buyuk(slayt.get('baslik', '')), f_b, W - 140)[:2]:
        d.text((73, y + 4), s, font=f_b, fill=(0, 0, 0, 190))
        d.text((70, y), s, font=f_b, fill=GOLD)
        y += int(f_b.size * 1.1)
    # metin
    y += 20
    f_m = font('govde', 50)
    for s in satirlara_bol(d, slayt.get('metin', ''), f_m, W - 140)[:5]:
        d.text((72, y + 3), s, font=f_m, fill=(0, 0, 0, 170))
        d.text((70, y), s, font=f_m, fill=WHITE)
        y += int(f_m.size * 1.35)
    sayfa_no(d, W, no, toplam)
    logo_koy(c, boy=110)
    return c.convert('RGB')


def son_slayt(sahne, urun, is_, slayt, W=1080, H=1350, no=3, toplam=3):
    c = kapla(sahne, W, H, blur=5).convert('RGBA')
    c.alpha_composite(Image.new('RGBA', (W, H), (0, 0, 0, 90)))
    c.alpha_composite(gradyan(W, H, ust=0.30, alt=0.62, ust_g=220, alt_g=250))
    urun_yerlestir(c, urun, W // 2, int(H * 0.62), int(H * 0.36), max_genislik=int(W * 0.5))
    d = ImageDraw.Draw(c)
    baslik = buyuk(slayt.get('baslik') or 'ŞİMDİ SİPARİŞ VER')
    y = 90
    for i, s in enumerate(satirlara_bol(d, baslik, font('baslik', 104), W - 120)[:2]):
        f = sigdir(d, s, 'baslik', 104, W - 120)
        ortala(d, W, s, y, f, WHITE if i == 0 else GOLD)
        y += int(f.size * 1.08)
    y = int(H * 0.68)
    f_m = font('govde', 40)
    for s in satirlara_bol(d, slayt.get('metin', ''), f_m, W - 180)[:2]:
        ortala(d, W, s, y, f_m, (235, 235, 235))
        y += int(f_m.size * 1.35)
    cta_hap(d, W, int(H * 0.80), is_.get('site', 'otomotodunyasi.com'), boyut=52)
    ortala(d, W, 'Link profilde  •  DM\'den sorabilirsin', int(H * 0.80) + 120, font('govde', 32), (230, 230, 230))
    sayfa_no(d, W, no, toplam)
    logo_koy(c, boy=int(H * 0.11))
    return c.convert('RGB')


def reels_video(gorsel_yolu, cikis, sure=8):
    komut = [
        'ffmpeg', '-y', '-loop', '1', '-i', gorsel_yolu,
        '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
        '-vf', ("scale=1296:2304,zoompan=z='min(zoom+0.0007,1.10)':d=%d:"
                "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30,format=yuv420p" % (sure * 30)),
        '-t', str(sure), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
        '-c:a', 'aac', '-b:a', '128k', '-shortest', '-movflags', '+faststart', cikis
    ]
    subprocess.run(komut, check=True, capture_output=True)


# ------------------------------------------------------------------ ana akış
def main():
    is_ = json.loads(os.environ['IS'])
    kimlik = str(is_['id']).strip()
    klasor = os.path.join(KOK, 'cikti', kimlik)
    os.makedirs(klasor, exist_ok=True)
    sonuc = {'id': kimlik, 'ok': False, 'zaman': time.strftime('%Y-%m-%d %H:%M:%S'), 'dosyalar': []}
    try:
        fmt = (is_.get('format') or 'Tekli').strip()
        sahne = sahne_uret(is_.get('sahne_istemi') or 'moody motorcycle garage at night')
        sahne.save(os.path.join(klasor, 'sahne.jpg'), quality=88)
        urun = arka_plan_sil(urun_indir(is_['urun_foto']))
        urun.save(os.path.join(klasor, 'urun.png'))

        if fmt == 'Reels':
            dikey = kapak(sahne, urun, is_, 1080, 1920)
            dikey.save(os.path.join(klasor, 'kapak.jpg'), quality=92)
            reels_video(os.path.join(klasor, 'kapak.jpg'), os.path.join(klasor, 'reels.mp4'))
            sonuc['dosyalar'] = ['reels.mp4']
            sonuc['kapak'] = 'kapak.jpg'
        else:
            slaytlar = is_.get('slaytlar') or []
            toplam = 1 if fmt == 'Tekli' else max(2, min(5, 1 + len(slaytlar)))
            gorseller = [kapak(sahne, urun, is_, no=1, toplam=toplam)]
            ara = slaytlar[:toplam - 1]
            for i, s in enumerate(ara):
                no = i + 2
                if no == toplam:
                    gorseller.append(son_slayt(sahne, urun, is_, s, no=no, toplam=toplam))
                else:
                    gorseller.append(bilgi(sahne, urun, is_, s, no=no, toplam=toplam))
            for i, g in enumerate(gorseller, 1):
                ad = f'{i}.jpg'
                g.save(os.path.join(klasor, ad), quality=92)
                sonuc['dosyalar'].append(ad)
        sonuc['ok'] = True
    except Exception as e:
        traceback.print_exc()
        sonuc['hata'] = f'{type(e).__name__}: {e}'
    with open(os.path.join(klasor, 'sonuc.json'), 'w', encoding='utf-8') as f:
        json.dump(sonuc, f, ensure_ascii=False, indent=2)
    print(json.dumps(sonuc, ensure_ascii=False))


if __name__ == '__main__':
    main()
