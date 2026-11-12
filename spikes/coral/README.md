# Spike M-V0: Coral M.2 on the Pi 5

Goal: find out **whether and how** the Coral Edge TPU (M.2) can be used
from Python on this Pi 5 (8 GB, Raspberry Pi OS Bookworm). The result goes
into `docs/target-architecture.md` (section 7.6 and milestone M-V0).

Acceptance:

- `/dev/apex_0` exists after booting.
- `bench.py` runs on the TPU with at least one backend.
- SSD MobileNet v2 inference on the TPU is clearly faster than on the CPU
  (guideline: median ≤ 20 ms).
- The camera is detected by `rpicam-hello --list-cameras`.

All steps with `sudo` change the system. Try them on an SD card you can
reflash if needed.

## 0. Inventory

```bash
./check_env.sh | tee env-before.txt
```

## 1. Check the mechanics before installing anything

Our setup: **Pineboards HatDrive! AI** (NVMe + E-key slot behind a PCIe
switch) with a **Coral Dual Edge TPU**. The HatDrive passes one PCIe lane
to the E-key slot, so only one of the two TPUs is expected to show up:
`lspci` should list the switch, the NVMe and one `089a` device, and only
`/dev/apex_0` should exist. Note what you actually see.

- **Card variant:** the Coral M.2 Accelerator comes with an **A+E key**
  and with a **B+M key**. M.2 HATs for the Pi 5 usually have an M-key
  slot, where only the B+M variant fits directly. The Dual Edge TPU card
  (E key) needs a special adapter, and only one TPU is usable on the Pi 5
  anyway.
- **Space on the header:** the PiCrawler's Robot HAT occupies the 40-pin
  GPIO header. Check whether your M.2 board fits underneath (form factors
  that sit below the Pi and only use the PCIe ribbon cable) or whether the
  Robot HAT can sit on a stacking header above it.
- **Camera:** the Pi 5 needs the 22-pin to 15-pin adapter cable for the
  original camera module.

## 2. PCIe and driver (once, with reboot)

These steps follow Jeff Geerling's guide (link below). Please check the
current state there, details change with kernel versions.

1. Add to `/boot/firmware/config.txt`:

   ```ini
   # 4K page kernel: the Coral driver does not work with 16K pages
   kernel=kernel8.img
   dtparam=pciex1
   # MSI compatibility for the Coral
   dtoverlay=pciex1-compat-pi5,no-mip
   ```

2. Reboot and check: `getconf PAGESIZE` must print `4096`, `lspci` must
   show a device with `089a`.
3. Install the **gasket** driver (`gasket-dkms`). Google's original package
   no longer builds on newer kernels; a maintained community fork is
   needed. Which one currently works is listed in the issues or the guide
   below.
4. udev rule so that not only root can access the TPU:

   ```bash
   sudo groupadd -f apex
   echo 'SUBSYSTEM=="apex", MODE="0660", GROUP="apex"' | sudo tee /etc/udev/rules.d/65-apex.rules
   sudo usermod -aG apex "$USER"   # later also: picrawler
   ```

5. Reboot. `ls -l /dev/apex_0` must show group `apex`.

## 3. Edge TPU runtime (`libedgetpu`)

```bash
echo "deb [signed-by=/usr/share/keyrings/coral-edgetpu.gpg] https://packages.cloud.google.com/apt coral-edgetpu-stable main" \
  | sudo tee /etc/apt/sources.list.d/coral-edgetpu.list
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/coral-edgetpu.gpg
sudo apt update
sudo apt install libedgetpu1-std   # standard clock, not -max (power/heat)
```

If that fails, there are community builds of `libedgetpu` for Bookworm.
Note which one you used.

## 4. Test models

```bash
mkdir -p models && cd models
base=https://github.com/google-coral/test_data/raw/master
curl -fLO $base/ssd_mobilenet_v2_coco_quant_postprocess_edgetpu.tflite   # TPU
curl -fLO $base/ssd_mobilenet_v2_coco_quant_postprocess.tflite           # CPU comparison
cd ..
```

## 5. Try the Python routes

Use a separate venv for each route so they do not interfere. Save the
output of `bench.py` for every route, even if it fails.

### Route B: Python 3.9 + official pycoral (try first)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # if uv is missing
uv venv --python 3.9 .venv-py39
. .venv-py39/bin/activate
uv pip install numpy~=1.24 --extra-index-url https://google-coral.github.io/py-repo/ pycoral~=2.0 tflite-runtime
python bench.py --model models/ssd_mobilenet_v2_coco_quant_postprocess_edgetpu.tflite | tee result-b.json
python bench.py --cpu --backend tflite_runtime --model models/ssd_mobilenet_v2_coco_quant_postprocess.tflite | tee result-b-cpu.json
deactivate
```

### Route A: system Python 3.11 + LiteRT/tflite-runtime + delegate

```bash
python3 -m venv .venv-py311
. .venv-py311/bin/activate
pip install numpy ai-edge-litert      # alternatively: tflite-runtime
python bench.py --backend ai_edge_litert --model models/ssd_mobilenet_v2_coco_quant_postprocess_edgetpu.tflite | tee result-a.json
deactivate
```

### Route C: community build for Python 3.11

```bash
python3 -m venv .venv-c
. .venv-c/bin/activate
pip install numpy pycoral-pi5
python bench.py --backend pycoral --model models/ssd_mobilenet_v2_coco_quant_postprocess_edgetpu.tflite | tee result-c.json
deactivate
```

## 6. Camera

```bash
rpicam-hello --list-cameras
rpicam-still -o test.jpg
```

## 7. Report the result

```bash
./check_env.sh > env-after.txt
cat env-after.txt result-*.json
```

That output is enough. From it, the chosen route, the measurements and the
required installation steps go into the architecture document and the
Ansible role.

## Sources

- Jeff Geerling: [A PCIe Coral TPU FINALLY works on Raspberry Pi 5](https://www.jeffgeerling.com/blog/2023/pcie-coral-tpu-finally-works-on-raspberry-pi-5/)
- [google-coral/pycoral issue #133](https://github.com/google-coral/pycoral/issues/133) (installation on the Pi 5)
- [pycoral-pi5 on PyPI](https://pypi.org/project/pycoral-pi5/)
