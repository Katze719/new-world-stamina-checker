
from pytesseract import pytesseract 
from fuzzywuzzy import fuzz  # pip install fuzzywuzzy
from PIL import Image
import cv2
  
# Defining paths to tesseract.exe 
# and the image we would be using 
pytesseract.tesseract_cmd = "/usr/bin/tesseract"

BASE_THRESHOLD = 80

def extractNamesFromImage(path, names):
    # Bild laden und vorverarbeiten
    img = cv2.imread(path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Umwandlung in ein PIL-Bild und OCR durchführen (Sprache z.B. Deutsch)
    pil_img = Image.fromarray(thresh)
    text = pytesseract.image_to_string(pil_img, lang="deu")

    # Liste der Nutzernamen, auch solche mit Leerzeichen

    def is_similar(user: str, text: str, threshold=BASE_THRESHOLD):
        """
        Prüft, ob der Nutzername (user) in irgendeiner n-Gramm-Kombination des OCR-Textes
        gefunden wird, wobei n der Anzahl der Wörter im Nutzernamen entspricht.
        """
        # Aufteilen des Nutzernamens in Wörter (z.B. ["Max", "Mustermann"])

        if "|| G.O.A.T ||" in user:
            user = user.replace(".", "").replace("|", "").replace(" ", "")

        user_words = user.split()
        num_words = len(user_words)
        # Aufteilen des gesamten Textes in Wörter
        text_words = text.split()
        
        # Falls der OCR-Text zu kurz ist, um einen Vergleich zu machen
        if len(text_words) < num_words:
            return False

        # "Sliding Window" über den OCR-Text: Wir bilden Gruppen von num_words Wörtern
        for i in range(len(text_words) - num_words + 1):
            # Fenster (n-Gramm) aus den nächsten num_words Wörtern
            window = " ".join(text_words[i:i+num_words])
            for wind in window.split("\n"):
                threshold = BASE_THRESHOLD
                if ".." in wind:
                    threshold = 60
                
                if "goat" in wind.lower() and "goat" in user.lower():
                    user = user.replace(".", "").replace("|", "").replace(" ", "")
                    threshold = 60

                similarity = fuzz.ratio(user.lower(), wind.lower())
                if similarity >= threshold:
                    return True
        return False

    found_names = []
    for nutzer in names:
        if is_similar(nutzer, text):
            found_names.append(nutzer)

    return found_names
