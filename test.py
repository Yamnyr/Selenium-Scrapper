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
parser.add_argument("--json_output", action="store_true", help="Sauvegarder aussi en JSON")
parser.add_argument("--headless", action="store_true", help="Exécuter en mode headless")
parser.add_argument("--delay", type=int, default=2, help="Délai entre les actions (secondes)")
args = parser.parse_args()

# --- Variables globales ---
doctors_data = []

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

def clean_text(text):
    if not text:
        return ""
    text = text.replace("\n", " ").replace("\r", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text

def parse_address(address_str):
    """Sépare l'adresse en rue, code postal, ville"""
    result = {"rue": "", "code_postal": "", "ville": ""}
    if not address_str:
        return result
    match = re.search(r"(\d{5})", address_str)
    if match:
        result["code_postal"] = match.group(1)
        parts = address_str.split(match.group(1))
        result["rue"] = clean_text(parts[0])
        result["ville"] = clean_text(parts[1]) if len(parts) > 1 else ""
    else:
        result["rue"] = clean_text(address_str)
    return result

def extract_doctor_info_from_list(doctor_element):
    info = {}
    try:
        name_element = doctor_element.find_element(By.CSS_SELECTOR, "h2")
        info['nom'] = clean_text(name_element.text)
    except NoSuchElementException:
        info['nom'] = "Nom non trouvé"

    try:
        specialty_element = doctor_element.find_element(By.CSS_SELECTOR, "p[data-design-system-component='Paragraph']")
        specialty_text = specialty_element.text.strip()
        if specialty_text and not any(word in specialty_text.lower() for word in ['rue', 'avenue', 'boulevard', 'km', 'conventionné']):
            info['specialite'] = clean_text(specialty_text)
        else:
            info['specialite'] = args.query
    except NoSuchElementException:
        info['specialite'] = args.query

    try:
        paragraphs = doctor_element.find_elements(By.CSS_SELECTOR, "p")
        address_parts = []
        for p in paragraphs:
            text = clean_text(p.text)
            if text and (re.search(r'\d{5}', text) or any(w in text.lower() for w in ['rue', 'avenue', 'boulevard', 'place'])):
                if text not in address_parts:
                    address_parts.append(text)
        info['adresse'] = ", ".join(address_parts[:2]) if address_parts else ""
    except Exception:
        info['adresse'] = ""

    try:
        distance_element = doctor_element.find_element(By.XPATH, ".//*[contains(text(), 'km') or contains(text(), 'm')]")
        info['distance'] = clean_text(distance_element.text)
    except NoSuchElementException:
        info['distance'] = ""

    try:
        conv_elements = doctor_element.find_elements(By.XPATH, ".//*[contains(text(), 'Conventionné') or contains(text(), 'conventionné')]")
        info['conventionnement'] = conv_elements[0].text.strip() if conv_elements else ""
    except Exception:
        info['conventionnement'] = ""

    try:
        rating_element = doctor_element.find_element(By.CSS_SELECTOR, "[data-test-id='review-summary-rating']")
        info['note'] = rating_element.text.strip()
    except NoSuchElementException:
        info['note'] = ""

    try:
        link = doctor_element.find_element(By.CSS_SELECTOR, "a[href*='/doctor'], a[href*='/medecin']")
        info['lien_profil'] = link.get_attribute("href")
    except NoSuchElementException:
        info['lien_profil'] = ""

    return info

def normalize_doctor_info(info):
    info['nom'] = clean_text(info.get('nom', ''))
    info['specialite'] = clean_text(info.get('specialite', args.query))

    addr = parse_address(info.get('adresse', ''))
    info['rue'] = addr['rue']
    info['code_postal'] = addr['code_postal']
    info['ville'] = addr['ville']
    del info['adresse']

    note = info.get('note', '').replace(',', '.')
    info['note'] = float(note) if re.match(r'^\d+(\.\d+)?$', note) else None

    info['conventionne'] = True if 'conventionné' in info.get('conventionnement', '').lower() else False
    del info['conventionnement']

    slots = info.get('creneaux_disponibles', '')
    info['creneaux_disponibles'] = "; ".join([clean_text(s) for s in slots.split(';')]) if slots else ""
    info['nb_creneaux'] = int(info.get('nb_creneaux', 0))

    info['tarifs_remboursement'] = clean_text(info.get('tarifs_remboursement', 'Non renseigné'))
    info['moyens_paiement'] = clean_text(info.get('moyens_paiement', 'Non renseigné'))
    info['expertises_actes'] = clean_text(info.get('expertises_actes', 'Non renseigné'))

    return info

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

    # --- Rejeter cookies ---
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

    # --- Scroll pour charger tous les résultats ---
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

    # --- Récupérer toutes les cartes des médecins ---
    doctor_cards = driver.find_elements(By.CSS_SELECTOR,
        "#main-content > div.flex.flex-1.flex-grow.flex-col.items-center > div > div.max-w-7xl > div.flex.gap-16.flex-col.w-full > div")

    doctors_count = min(len(doctor_cards), args.max_results)
    print(f"Analyse de {doctors_count} médecins...")

    for i, card in enumerate(doctor_cards[:doctors_count], 1):
        print(f"\nMédecin {i}/{doctors_count}")
        doctor_info = extract_doctor_info_from_list(card)
        doctor_info['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Cliquer sur "Voir plus"
        try:
            while True:
                try:
                    voir_plus_btn = card.find_element(By.XPATH, ".//button[contains(., 'Voir plus')]")
                    driver.execute_script("arguments[0].click();", voir_plus_btn)
                    time.sleep(1)
                except NoSuchElementException:
                    break
        except Exception as e:
            print(f"Erreur clic 'Voir plus': {e}")

        # Créneaux disponibles
        try:
            available_slots = []
            slot_elements = card.find_elements(By.CSS_SELECTOR,
                "button[data-test-id*='slot'], .dl-booking-slot, .available-slot, .slot-time, .calendar-slot")
            for slot in slot_elements:
                slot_text = clean_text(slot.text)
                if slot_text and slot_text not in available_slots:
                    available_slots.append(slot_text)
            doctor_info['creneaux_disponibles'] = '; '.join(available_slots) if available_slots else ""
            doctor_info['nb_creneaux'] = len(available_slots)
        except Exception:
            doctor_info['creneaux_disponibles'] = ""
            doctor_info['nb_creneaux'] = 0

        # Profil médecin
        profile_link = doctor_info.get('lien_profil', '')
        if profile_link:
            try:
                driver.execute_script("window.open(arguments[0], '_blank');", profile_link)
                driver.switch_to.window(driver.window_handles[-1])
                time.sleep(3)

                try:
                    tarif_section = driver.find_element(By.CSS_SELECTOR, "#payment_means .dl-profile-text")
                    doctor_info['tarifs_remboursement'] = clean_text(tarif_section.text)
                except NoSuchElementException:
                    doctor_info['tarifs_remboursement'] = ""

                try:
                    payment_section = driver.find_element(By.CSS_SELECTOR, "#payment_means ~ div .dl-profile-text")
                    doctor_info['moyens_paiement'] = clean_text(payment_section.text)
                except NoSuchElementException:
                    doctor_info['moyens_paiement'] = ""

                try:
                    skills_section = driver.find_element(By.CSS_SELECTOR, "#skills .dl-profile-skills")
                    skills = [s.text for s in skills_section.find_elements(By.CSS_SELECTOR, ".dl-profile-skill-chip")]
                    doctor_info['expertises_actes'] = "; ".join([clean_text(s) for s in skills]) if skills else ""
                except NoSuchElementException:
                    doctor_info['expertises_actes'] = ""

                driver.close()
                driver.switch_to.window(driver.window_handles[0])
            except Exception as e:
                print(f"Erreur récupération profil: {e}")
                driver.close()
                driver.switch_to.window(driver.window_handles[0])

        # Normaliser les données
        doctor_info = normalize_doctor_info(doctor_info)
        doctors_data.append(doctor_info)
        print(f"✅ Médecin traité: {doctor_info.get('nom', 'Nom inconnu')}")

finally:
    if doctors_data:
        # --- Sauvegarde CSV ---
        csv_filename = args.output
        with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = [
                'nom', 'specialite', 'rue', 'code_postal', 'ville', 'note', 'distance',
                'lien_profil', 'conventionne', 'creneaux_disponibles', 'nb_creneaux',
                'tarifs_remboursement', 'moyens_paiement', 'expertises_actes', 'timestamp'
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
