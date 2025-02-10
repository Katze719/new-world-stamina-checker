import cv2
import numpy as np
import matplotlib

matplotlib.use('Agg')  # Nutzt ein nicht-interaktives Backend für Speicherung
import matplotlib.pyplot as plt


# Funktion zur Generierung eines Farbverlaufs im HSV-Farbraum
def generate_hsv_gradient(
    hue_range=(50, 60), saturation_range=(0, 255), value_range=(0, 255), width=3000, height=100
):
    """
    Erstellt eine Farbverlaufskarte für die angegebenen HSV-Werte.
    - hue_range: Bereich für den Farbton (H), z. B. (50, 60) für Gelbtöne.
    - saturation_range: Bereich für die Sättigung (S), z. B. (0, 255) für Graustufen.
    - value_range: Bereich für die Helligkeit (V), z. B. (0, 255) für Schwarz-Weiß-Abstufungen.
    - width: Breite des Bildes.
    - height: Höhe des Bildes.
    """

    # Erstellt ein leeres Bild mit der Höhe und Breite
    gradient = np.zeros((height, width, 3), dtype=np.uint8)

    # Farbverläufe durchlaufen
    for x in range(width):
        # Interpolieren der Werte entlang der Breite
        hue = int(hue_range[0] + (hue_range[1] - hue_range[0]) * (x / width))
        saturation = int(
            saturation_range[0] + (saturation_range[1] - saturation_range[0]) * (x / width)
        )
        value = int(value_range[0] + (value_range[1] - value_range[0]) * (x / width))

        # HSV-Farbe setzen (H=0-179 in OpenCV, S/V=0-255)
        gradient[:, x] = (hue, saturation, value)

    # Konvertieren von HSV zu BGR für die Darstellung
    gradient_bgr = cv2.cvtColor(gradient, cv2.COLOR_HSV2RGB)

    return gradient_bgr

def hex_in_spectrum(hex_color, hue_range=(50, 60), saturation_range=(0, 255), value_range=(0, 255)):
    """
    Prüft, ob ein gegebener Hex-Farbwert innerhalb des definierten HSV-Spektrums liegt.
    """
    # Hex in RGB umwandeln
    hex_color = hex_color.lstrip('#')
    r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    
    # RGB zu HSV konvertieren
    hsv_color = cv2.cvtColor(np.uint8([[[r, g, b]]]), cv2.COLOR_RGB2HSV)[0][0]
    
    # Prüfen, ob die Werte innerhalb des Bereichs liegen
    h, s, v = hsv_color
    return (hue_range[0] <= h <= hue_range[1] and
            saturation_range[0] <= s <= saturation_range[1] and
            value_range[0] <= v <= value_range[1])
    
def get_hsv_range_from_hex_list(hex_list):
    """
    Bestimmt die unteren und oberen HSV-Grenzwerte für eine Liste von Hex-Farben.
    """
    hsv_values = []
    for hex_color in hex_list:
        hex_color = hex_color.lstrip('#')
        r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        hsv = cv2.cvtColor(np.uint8([[[r, g, b]]]), cv2.COLOR_RGB2HSV)[0][0]
        hsv_values.append(hsv)
    
    hsv_values = np.array(hsv_values)
    lower_bound = np.min(hsv_values, axis=0)
    upper_bound = np.max(hsv_values, axis=0)
    
    return tuple(lower_bound), tuple(upper_bound)


hex_list = ["#9F7E1A", "#5F5528", "#5D5825", "#A78B16", "#8D6A13", "#c58f37", "#cc8438", "#bf873d", "#c68f4e"]

t = get_hsv_range_from_hex_list(hex_list)

print(t)

# Erzeuge mehrere Verläufe:
gradient1 = generate_hsv_gradient(
    hue_range=(15, 28), saturation_range=(148, 221), value_range=(93, 204)
)  # Reine Gelbtöne
gradient2 = generate_hsv_gradient(
    hue_range=(21, 28), saturation_range=(148, 255), value_range=(93, 200)
)  # Mit Graustufen
gradient3 = generate_hsv_gradient(
    hue_range=(15, 28), saturation_range=(148, 221), value_range=(93, 255)
)  # Mit Schwarz-Weiß-Tönen

# # Anzeige der Farbverläufe
fig, axs = plt.subplots(3, 1, figsize=(20, 10), dpi=200)

axs[0].imshow(gradient1)
axs[0].set_title("Pure Hue")
axs[0].axis("off")

axs[1].imshow(gradient2)
axs[1].set_title("Over Saturated")
axs[1].axis("off")

axs[2].imshow(gradient3)
axs[2].set_title("Over Valued")
axs[2].axis("off")

plt.savefig("hsv_gradient.png")
plt.close()
