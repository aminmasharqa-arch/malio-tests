from pathlib import Path

import cv2
import pytesseract
from pytesseract import Output


# -------------------------------------------------
# CONFIG
# -------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
IMAGE_PATH = BASE_DIR / "photo_2.jpg"
RESULTS_DIR = BASE_DIR / "results"

TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

# Main language choice for this invoice:
# Hebrew is dominant, English is useful for email / latin text.
LANGUAGES = [
    "heb+eng",
    "eng+heb",
]

# Best PSM candidates for this invoice layout
PSM_MODES = [4, 11]


# -------------------------------------------------
# VALIDATION
# -------------------------------------------------

if not IMAGE_PATH.exists():
    raise FileNotFoundError(
        f"Image not found:\n{IMAGE_PATH}"
    )

RESULTS_DIR.mkdir(exist_ok=True)


# -------------------------------------------------
# LOAD IMAGE
# -------------------------------------------------

original = cv2.imread(str(IMAGE_PATH))

if original is None:
    raise RuntimeError(
        f"OpenCV could not read image:\n{IMAGE_PATH}"
    )


# -------------------------------------------------
# PREPROCESSING
# -------------------------------------------------

gray = cv2.cvtColor(
    original,
    cv2.COLOR_BGR2GRAY
)

# Upscale to help OCR
upscaled = cv2.resize(
    gray,
    None,
    fx=2,
    fy=2,
    interpolation=cv2.INTER_CUBIC
)

# Light denoising
blurred = cv2.GaussianBlur(
    upscaled,
    (3, 3),
    0
)

# Adaptive thresholding
thresholded = cv2.adaptiveThreshold(
    blurred,
    255,
    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
    cv2.THRESH_BINARY,
    31,
    11
)


# Save preprocessing outputs
cv2.imwrite(
    str(RESULTS_DIR / "01_gray.jpg"),
    gray
)

cv2.imwrite(
    str(RESULTS_DIR / "02_upscaled.jpg"),
    upscaled
)

cv2.imwrite(
    str(RESULTS_DIR / "03_thresholded.jpg"),
    thresholded
)


# -------------------------------------------------
# TEST IMAGES
# -------------------------------------------------

images_to_test = {
    "original": original,
    "gray": gray,
    "thresholded": thresholded,
}


# -------------------------------------------------
# OCR LOOP
# -------------------------------------------------

for image_name, image in images_to_test.items():

    for language in LANGUAGES:

        for psm in PSM_MODES:

            config = f"--oem 1 --psm {psm}"

            print("\n" + "=" * 80)
            print(
                f"IMAGE={image_name} | "
                f"LANG={language} | "
                f"PSM={psm}"
            )
            print("=" * 80)

            # -----------------------------------------
            # OCR TEXT
            # -----------------------------------------

            text = pytesseract.image_to_string(
                image,
                lang=language,
                config=config,
            )

            print(text)

            safe_lang = language.replace("+", "_")

            text_filename = (
                f"{image_name}"
                f"__{safe_lang}"
                f"__psm_{psm}.txt"
            )

            text_path = RESULTS_DIR / text_filename

            text_path.write_text(
                text,
                encoding="utf-8"
            )

            # -----------------------------------------
            # OCR DATA
            # -----------------------------------------

            data = pytesseract.image_to_data(
                image,
                lang=language,
                config=config,
                output_type=Output.DICT,
            )

            rows = []

            for i, word in enumerate(data["text"]):

                word = word.strip()

                if not word:
                    continue

                try:
                    confidence = float(data["conf"][i])
                except Exception:
                    confidence = -1

                rows.append(
                    {
                        "text": word,
                        "confidence": confidence,
                        "left": data["left"][i],
                        "top": data["top"][i],
                        "width": data["width"][i],
                        "height": data["height"][i],
                    }
                )

            # -----------------------------------------
            # SAVE WORD DATA
            # -----------------------------------------

            csv_filename = (
                f"{image_name}"
                f"__{safe_lang}"
                f"__psm_{psm}.csv"
            )

            csv_path = RESULTS_DIR / csv_filename

            with open(
                csv_path,
                "w",
                encoding="utf-8-sig"
            ) as f:

                f.write(
                    "text,confidence,left,top,width,height\n"
                )

                for row in rows:

                    safe_text = (
                        row["text"]
                        .replace('"', '""')
                    )

                    f.write(
                        f'"{safe_text}",'
                        f'{row["confidence"]},'
                        f'{row["left"]},'
                        f'{row["top"]},'
                        f'{row["width"]},'
                        f'{row["height"]}\n'
                    )

            # -----------------------------------------
            # DRAW OCR BOXES
            # -----------------------------------------

            preview = image.copy()

            if len(preview.shape) == 2:
                preview = cv2.cvtColor(
                    preview,
                    cv2.COLOR_GRAY2BGR
                )

            for row in rows:

                if row["confidence"] < 50:
                    continue

                x = row["left"]
                y = row["top"]
                w = row["width"]
                h = row["height"]

                cv2.rectangle(
                    preview,
                    (x, y),
                    (x + w, y + h),
                    (0, 255, 0),
                    1,
                )

            preview_filename = (
                f"{image_name}"
                f"__{safe_lang}"
                f"__psm_{psm}"
                f"__boxes.jpg"
            )

            cv2.imwrite(
                str(
                    RESULTS_DIR
                    / preview_filename
                ),
                preview
            )


print("\n" + "=" * 80)
print("DONE")
print("=" * 80)
print(f"Results saved to:")
print(RESULTS_DIR)