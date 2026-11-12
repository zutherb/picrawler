# Spike M-V0: Coral M.2 auf dem Pi 5

Ziel: herausfinden, **ob und wie** die Coral Edge TPU (M.2) auf diesem
Pi 5 (8 GB, Raspberry Pi OS Bookworm) aus Python nutzbar ist. Ergebnis
kommt in `docs/target-architecture.md` (Abschnitt 7.6 und Meilenstein M-V0).

Abnahme:

- `/dev/apex_0` existiert nach dem Booten.
- `bench.py` läuft mit mindestens einem Backend auf der TPU.
- Die Inferenz von SSD MobileNet v2 auf der TPU ist deutlich schneller als
  auf der CPU (Richtwert: Median ≤ 20 ms).
- Die Kamera wird von `rpicam-hello --list-cameras` erkannt.

Alle Schritte mit `sudo` ändern das System. Ausprobieren auf einer SD-Karte,
die du notfalls neu aufsetzen kannst.

## 0. Bestandsaufnahme

```bash
./check_env.sh | tee env-before.txt
```

## 1. Mechanik prüfen, bevor du etwas installierst

- **Kartenvariante:** Den Coral-M.2-Accelerator gibt es mit **A+E-Key**
  und mit **B+M-Key**. M.2-HATs für den Pi 5 haben meist einen M-Key-Slot.
  Dort passt nur die B+M-Variante direkt. Die Dual-Edge-TPU-Karte
  (E-Key) braucht einen Spezialadapter, und am Pi 5 ist ohnehin nur eine
  TPU nutzbar.
- **Platz auf dem Header:** Der Robot HAT des PiCrawler belegt die
  40-polige GPIO-Leiste. Prüfe, ob dein M.2-Board darunter passt
  (Bauformen für unter dem Pi, die nur das PCIe-Flachbandkabel nutzen) oder
  ob der Robot HAT auf einer Stapelleiste darüber sitzen kann.
- **Kamera:** Der Pi 5 braucht für das Original-Kameramodul das
  Adapterkabel 22-polig auf 15-polig.

## 2. PCIe und Treiber (einmalig, mit Reboot)

Die Schritte folgen Jeff Geerlings Anleitung (Link unten). Bitte dort den
aktuellen Stand gegenprüfen, Details ändern sich mit Kernel-Versionen.

1. In `/boot/firmware/config.txt` ergänzen:

   ```ini
   # 4K-Page-Kernel: der Coral-Treiber funktioniert nicht mit 16K-Pages
   kernel=kernel8.img
   dtparam=pciex1
   # MSI-Kompatibilität für die Coral
   dtoverlay=pciex1-compat-pi5,no-mip
   ```

2. Neustarten und prüfen: `getconf PAGESIZE` muss `4096` ausgeben,
   `lspci` muss ein Gerät mit `089a` zeigen.
3. Den **gasket**-Treiber (`gasket-dkms`) installieren. Das Original-Paket
   von Google baut auf neueren Kernels nicht mehr. Nötig ist ein gepflegter
   Community-Fork. Welcher aktuell funktioniert, steht in den Issues bzw.
   der Anleitung unten.
4. udev-Regel, damit nicht nur root auf die TPU zugreifen darf:

   ```bash
   sudo groupadd -f apex
   echo 'SUBSYSTEM=="apex", MODE="0660", GROUP="apex"' | sudo tee /etc/udev/rules.d/65-apex.rules
   sudo usermod -aG apex "$USER"   # später auch: picrawler
   ```

5. Neustarten. `ls -l /dev/apex_0` muss Gruppe `apex` zeigen.

## 3. Edge-TPU-Runtime (`libedgetpu`)

```bash
echo "deb [signed-by=/usr/share/keyrings/coral-edgetpu.gpg] https://packages.cloud.google.com/apt coral-edgetpu-stable main" \
  | sudo tee /etc/apt/sources.list.d/coral-edgetpu.list
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/coral-edgetpu.gpg
sudo apt update
sudo apt install libedgetpu1-std   # Standardtakt, nicht -max (Strom/Wärme)
```

Schlägt das fehl, gibt es Community-Builds von `libedgetpu` für Bookworm.
Vermerke dann, welchen du genommen hast.

## 4. Testmodelle

```bash
mkdir -p models && cd models
base=https://github.com/google-coral/test_data/raw/master
curl -fLO $base/ssd_mobilenet_v2_coco_quant_postprocess_edgetpu.tflite   # TPU
curl -fLO $base/ssd_mobilenet_v2_coco_quant_postprocess.tflite           # CPU-Vergleich
cd ..
```

## 5. Python-Wege ausprobieren

Jeden Weg in einem eigenen venv, damit sie sich nicht gegenseitig stören.
Bei jedem Weg die Ausgabe von `bench.py` sichern, auch wenn er scheitert.

### Weg B: Python 3.9 + offizielles pycoral (zuerst probieren)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # falls uv fehlt
uv venv --python 3.9 .venv-py39
. .venv-py39/bin/activate
uv pip install numpy~=1.24 --extra-index-url https://google-coral.github.io/py-repo/ pycoral~=2.0 tflite-runtime
python bench.py --model models/ssd_mobilenet_v2_coco_quant_postprocess_edgetpu.tflite | tee result-b.json
python bench.py --cpu --backend tflite_runtime --model models/ssd_mobilenet_v2_coco_quant_postprocess.tflite | tee result-b-cpu.json
deactivate
```

### Weg A: System-Python 3.11 + LiteRT/tflite-runtime + Delegate

```bash
python3 -m venv .venv-py311
. .venv-py311/bin/activate
pip install numpy ai-edge-litert      # alternativ: tflite-runtime
python bench.py --backend ai_edge_litert --model models/ssd_mobilenet_v2_coco_quant_postprocess_edgetpu.tflite | tee result-a.json
deactivate
```

### Weg C: Community-Build für Python 3.11

```bash
python3 -m venv .venv-c
. .venv-c/bin/activate
pip install numpy pycoral-pi5
python bench.py --backend pycoral --model models/ssd_mobilenet_v2_coco_quant_postprocess_edgetpu.tflite | tee result-c.json
deactivate
```

## 6. Kamera

```bash
rpicam-hello --list-cameras
rpicam-still -o test.jpg
```

## 7. Ergebnis zurückmelden

```bash
./check_env.sh > env-after.txt
cat env-after.txt result-*.json
```

Die Ausgabe reicht mir. Daraus trage ich den gewählten Weg, die Messwerte
und die nötigen Installationsschritte ins Architekturdokument und in die
Ansible-Rolle ein.

## Quellen

- Jeff Geerling: [A PCIe Coral TPU FINALLY works on Raspberry Pi 5](https://www.jeffgeerling.com/blog/2023/pcie-coral-tpu-finally-works-on-raspberry-pi-5/)
- [google-coral/pycoral Issue #133](https://github.com/google-coral/pycoral/issues/133) (Installation auf dem Pi 5)
- [pycoral-pi5 auf PyPI](https://pypi.org/project/pycoral-pi5/)
