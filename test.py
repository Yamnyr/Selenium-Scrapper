import argparse
import csv
import time
import re
import json
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# --- Arguments CLI ---
parser = argparse.ArgumentParser(description="Scraping Doctolib disponibilités")
parser.add_argument("--query", type=str, required=True, help="Requête médicale (ex: infirmier, généraliste)")
parser.add_argument("--location", type=str, required=True, help="Localisation (ex: 75015, Paris)")
parser.add_argument("--max_results", type=int, default=10, help="Nombre maximum de médecins à analyser")
parser.add_argument("--filters", type=str, default="", help="Filtres à appliquer (ex: disponibilites,langues:Anglais)")
parser.add_argument("--output", type=str, default="doctolib_results.csv", help="Fichier de sortie CSV")
parser.add_argument("--headless", action="store_true", help="Exécuter en mode headless")
parser.add_argument("--json_output", action="store_true", help="Sauvegarder aussi en JSON")
parser.add_argument("--delay", type=int, default=2, help="Délai entre les actions (secondes)")
args = parser.parse_args()

# --- Variables globales ---
doctors_data = []  # toujours défini au départ

# --- Fonctions utilitaires ---
def parse_filters(filters_str):
    filters = {}
    if not filters_str:
        return filters
    filter_items = filters_str.split(',')
    for item in filter_items:
        if ':' in item:
            key, value = item.split(':', 1)
            filters[key.strip()] = value.strip()
        else:
            filters[item.strip()] = True
    return filters


def extract_doctor_info_from_list(doctor_element):
    """Extrait les infos principales d’un médecin depuis la liste de résultats"""
    info = {}

    # --- NOM ---
    try:
        name_element = doctor_element.find_element(By.CSS_SELECTOR, "h2")
        info['nom'] = name_element.text.strip()
    except NoSuchElementException:
        info['nom'] = "Nom non trouvé"

    # --- SPÉCIALITÉ ---
    try:
        specialty_element = doctor_element.find_element(By.CSS_SELECTOR, "p[data-design-system-component='Paragraph']")
        specialty_text = specialty_element.text.strip()
        if specialty_text and not any(word in specialty_text.lower() for word in ['rue', 'avenue', 'boulevard', 'km', 'conventionné']):
            info['specialite'] = specialty_text
        else:
            info['specialite'] = args.query
    except NoSuchElementException:
        info['specialite'] = args.query

    # --- ADRESSE ---
    try:
        paragraphs = doctor_element.find_elements(By.CSS_SELECTOR, "p")
        address_parts = []
        for p in paragraphs:
            text = p.text.strip()
            if text and (re.search(r'\d{5}', text) or any(w in text.lower() for w in ['rue', 'avenue', 'boulevard', 'place'])):
                if text not in address_parts:
                    address_parts.append(text)
        info['adresse'] = ", ".join(address_parts[:2]) if address_parts else "Adresse non trouvée"
    except Exception:
        info['adresse'] = "Erreur d'extraction"

    # --- DISTANCE ---
    try:
        distance_element = doctor_element.find_element(By.XPATH, ".//*[contains(text(), 'km') or contains(text(), 'm')]")
        info['distance'] = distance_element.text.strip()
    except NoSuchElementException:
        info['distance'] = "Distance non trouvée"

    # --- CONVENTIONNEMENT ---
    try:
        conv_elements = doctor_element.find_elements(By.XPATH, ".//*[contains(text(), 'Conventionné') or contains(text(), 'conventionné')]")
        info['conventionnement'] = conv_elements[0].text.strip() if conv_elements else "Non spécifié"
    except Exception:
        info['conventionnement'] = "Non spécifié"

    # --- NOTE (si présente) ---
    try:
        rating_element = doctor_element.find_element(By.CSS_SELECTOR, "[data-test-id='review-summary-rating']")
        info['note'] = rating_element.text.strip()
    except NoSuchElementException:
        info['note'] = "Non renseignée"

    # --- LIEN PROFIL ---
    try:
        link = doctor_element.find_element(By.CSS_SELECTOR, "a[href*='/doctor'], a[href*='/medecin']")
        info['lien_profil'] = link.get_attribute("href")
    except NoSuchElementException:
        info['lien_profil'] = "Lien non trouvé"

    return info


def get_available_slots(driver, max_slots=10):
    """Extrait les créneaux disponibles sur la page d’un médecin"""
    available_slots = []
    time.sleep(args.delay)

    slot_selectors = [
        "button[data-test-id*='slot']",
        ".dl-booking-slot",
        ".available-slot",
        ".slot-time",
        ".calendar-slot"
    ]

    slots = []
    for selector in slot_selectors:
        slots = driver.find_elements(By.CSS_SELECTOR, selector)
        if slots:
            break

    for slot in slots[:max_slots]:
        try:
            slot_text = slot.text.strip()
            if slot_text and re.search(r'\d{1,2}[h:]\d{0,2}', slot_text):
                if slot_text not in available_slots:
                    available_slots.append(slot_text)
        except Exception:
            continue

    if not available_slots:
        available_slots = ["Aucun créneau visible ou agenda fermé"]

    return available_slots


# --- Selenium setup ---
options = Options()
options.add_argument("--window-size=1920,1080")
options.add_argument("--start-maximized")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option('useAutomationExtension', False)
options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
if args.headless:
    options.add_argument("--headless")

service = Service(ChromeDriverManager().install())

filters = parse_filters(args.filters)

try:
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    wait = WebDriverWait(driver, 15)

    driver.get("https://www.doctolib.fr/")

    # --- Rejeter cookies si présent ---
    try:
        reject_btn = wait.until(EC.element_to_be_clickable((By.ID, "didomi-notice-disagree-button")))
        reject_btn.click()
    except TimeoutException:
        pass

    # --- Recherche ---
    try:
        search_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input.searchbar-query-input")))
        search_input.clear()
        search_input.send_keys(args.query)
        time.sleep(1)

        place_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input.searchbar-place-input")))
        place_input.clear()
        place_input.send_keys(args.location)
        time.sleep(1)

        submit_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.searchbar-submit-button")))
        submit_btn.click()
        time.sleep(5)
    except Exception:
        print("Erreur lors de la recherche")
        driver.quit()
        exit()

    # --- Scroll pour charger les résultats ---
    try:
        last_height = driver.execute_script("return document.body.scrollHeight")
        while True:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height
        print("Scroll terminé, résultats chargés.")
    except Exception as e:
        print(f"Erreur pendant le scroll: {e}")

    # --- Récupérer médecins ---
    doctors = driver.find_elements(By.CSS_SELECTOR, "[data-test-id*='result'], [data-testid*='result'], .dl-search-result, .doctor-card, .practitioner-card")
    if not doctors:
        doctors = driver.find_elements(By.CSS_SELECTOR, "a[href*='/doctor'], a[href*='/medecin']")
    if not doctors:
        print("Aucun médecin trouvé")
        driver.quit()
        exit()

    doctors_to_process = doctors[:args.max_results]
    print(f"Analyse de {len(doctors_to_process)} médecins...")

    for i, doctor in enumerate(doctors_to_process, 1):
        print(f"Traitement du médecin {i}/{len(doctors_to_process)}")
        try:
            doctor_info = extract_doctor_info_from_list(doctor)
            doctor_info['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # --- Clic sur "Voir plus de créneaux" si présent ---
            try:
                while True:
                    try:
                        voir_plus_btn = doctor.find_element(By.XPATH, ".//button[contains(., 'Voir plus')]")
                        driver.execute_script("arguments[0].click();", voir_plus_btn)
                        print("Bouton 'Voir plus' cliqué (liste)")
                        time.sleep(2)
                    except NoSuchElementException:
                        break
                    except Exception as e:
                        print(f"Erreur lors du clic sur 'Voir plus': {e}")
                        break

                # --- Récupération des créneaux directement depuis la liste ---
                available_slots = []
                try:
                    slot_elements = doctor.find_elements(By.CSS_SELECTOR, "button[data-test-id*='slot'], .dl-booking-slot, .available-slot, .slot-time, .calendar-slot")
                    for slot in slot_elements[:10]:
                        slot_text = slot.text.strip()
                        if slot_text and re.search(r'\d{1,2}[h:]\d{0,2}', slot_text):
                            if slot_text not in available_slots:
                                available_slots.append(slot_text)
                except Exception:
                    pass

                if not available_slots:
                    available_slots = ["Aucun créneau visible depuis la liste"]

                doctor_info['creneaux_disponibles'] = '; '.join(available_slots)
                doctor_info['nb_creneaux'] = len(available_slots)

            except Exception as e:
                print(f"Erreur sur l'extraction des créneaux en liste: {e}")
                doctor_info['creneaux_disponibles'] = "Erreur en liste"
                doctor_info['nb_creneaux'] = -1

            doctors_data.append(doctor_info)
            print(f"✅ Médecin {i} traité: {doctor_info.get('nom', 'Nom inconnu')}")

        except Exception as e:
            print(f"Erreur sur médecin {i}: {e}")
            continue


finally:
    if doctors_data:
        # --- Sauvegarde CSV ---
        csv_filename = args.output
        with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = [
                'nom', 'specialite', 'adresse', 'note', 'distance',
                'lien_profil', 'conventionnement', 'creneaux_disponibles',
                'nb_creneaux', 'timestamp'
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for doctor in doctors_data:
                writer.writerow(doctor)
        print(f"Résultats sauvegardés dans {csv_filename} ({len(doctors_data)} médecins)")

        # --- Sauvegarde JSON ---
        if args.json_output:
            json_filename = args.output.replace('.csv', '.json')
            with open(json_filename, 'w', encoding='utf-8') as jsonfile:
                json.dump(doctors_data, jsonfile, ensure_ascii=False, indent=2)
            print(f"Résultats JSON sauvegardés dans {json_filename}")
    else:
        print("Aucune donnée collectée")

    driver.quit()
