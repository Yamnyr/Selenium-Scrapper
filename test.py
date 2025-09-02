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
    """
    Extrait les informations d'un médecin depuis l'élément de la liste de résultats
    Basé sur la vraie structure HTML de Doctolib
    """
    info = {}
    
    # --- NOM DU MÉDECIN ---
    try:
        # Le nom est dans un h2 avec les classes spécifiques
        name_element = doctor_element.find_element(
            By.CSS_SELECTOR, 
            "h2.dl-text.dl-text-body.dl-text-bold.dl-text-s.dl-text-primary-110"
        )
        info['nom'] = name_element.text.strip()
    except NoSuchElementException:
        # Fallback sur d'autres sélecteurs possibles
        try:
            name_element = doctor_element.find_element(By.CSS_SELECTOR, "h2")
            info['nom'] = name_element.text.strip()
        except NoSuchElementException:
            try:
                # Chercher via le lien qui contient le nom
                name_link = doctor_element.find_element(By.CSS_SELECTOR, "a[href*='/infirmier/'], a[href*='/medecin/'], a[href*='/doctors/']")
                name_element = name_link.find_element(By.CSS_SELECTOR, "h2")
                info['nom'] = name_element.text.strip()
            except NoSuchElementException:
                info['nom'] = "Nom non trouvé"

    # --- SPÉCIALITÉ ---
    try:
        # La spécialité est dans un paragraphe avec la classe XZWvFVZmM9FHf461kjNO
        specialty_element = doctor_element.find_element(
            By.CSS_SELECTOR, 
            "p.XZWvFVZmM9FHf461kjNO.G5dSlmEET4Zf5bQ5PR69"
        )
        specialty_text = specialty_element.text.strip()
        # Filtrer les éléments qui ne sont pas la spécialité
        if specialty_text and not any(word in specialty_text.lower() for word in ['rue', 'avenue', 'boulevard', 'km', 'conventionné']):
            info['specialite'] = specialty_text
        else:
            info['specialite'] = args.query
    except NoSuchElementException:
        # Fallback
        try:
            # Chercher tous les paragraphes et prendre le premier qui ressemble à une spécialité
            paragraphs = doctor_element.find_elements(By.CSS_SELECTOR, "p[data-design-system-component='Paragraph']")
            for p in paragraphs:
                text = p.text.strip()
                if text and not any(word in text.lower() for word in ['rue', 'avenue', 'boulevard', 'km', 'conventionné', 'adresse']):
                    info['specialite'] = text
                    break
            if 'specialite' not in info:
                info['specialite'] = args.query
        except:
            info['specialite'] = args.query

    # --- ADRESSE --- 
    try:
        address_parts = []
        
        try:
            address_container = doctor_element.find_element(
                By.CSS_SELECTOR, 
                "div.flex.flex-wrap.gap-x-4"
            )
            address_elements = address_container.find_elements(
                By.CSS_SELECTOR, 
                "p.XZWvFVZmM9FHf461kjNO.G5dSlmEET4Zf5bQ5PR69"
            )
            
            for elem in address_elements:
                text = elem.text.strip()
                if text and text not in address_parts: 
                    address_parts.append(text)
            
            if len(address_parts) > 2:
                address_parts = address_parts[:2]
                
        except NoSuchElementException:
            try:
                address_paragraphs = doctor_element.find_elements(
                    By.CSS_SELECTOR, 
                    "p.XZWvFVZmM9FHf461kjNO.G5dSlmEET4Zf5bQ5PR69"
                )
                
                for p in address_paragraphs:
                    text = p.text.strip()
                    if text and (
                        any(word in text.lower() for word in ['rue', 'avenue', 'boulevard', 'place', 'allée', 'chemin', 'impasse']) or
                        re.search(r'\b\d{5}\s+[A-Za-zÀ-ÿ\-\s]+\b', text) 
                    ):
                        if text not in address_parts:
                            address_parts.append(text)
                
                if len(address_parts) > 2:
                    address_parts = address_parts[:2]
                    
            except Exception:
                pass
        
        if address_parts:
            info['adresse'] = ", ".join(address_parts)
        else:
            try:
                location_icon = doctor_element.find_element(
                    By.CSS_SELECTOR, 
                    "svg[data-test-id='healthcare-provider-icon'][aria-label='Adresse']"
                )
                parent = location_icon.find_element(By.XPATH, "../..")
                address_elements = parent.find_elements(By.CSS_SELECTOR, "p")
                
                address_texts = []
                for elem in address_elements:
                    text = elem.text.strip()
                    if text and len(text) > 3:
                        address_texts.append(text)
                
                relevant_addresses = []
                for text in address_texts:
                    if any(word in text.lower() for word in ['rue', 'avenue', 'boulevard', 'place']) or \
                    re.search(r'\d{5}', text):
                        relevant_addresses.append(text)
                        if len(relevant_addresses) >= 2:
                            break
                
                info['adresse'] = ", ".join(relevant_addresses) if relevant_addresses else "Adresse non trouvée"
                
            except NoSuchElementException:
                info['adresse'] = "Adresse non trouvée"
                    
    except Exception as e:
        print(f"Erreur lors de l'extraction de l'adresse: {e}")
        info['adresse'] = "Erreur d'extraction"

    # --- DISTANCE --- 
    try:
        distance_element = doctor_element.find_element(
            By.CSS_SELECTOR, 
            "#main-content > div.flex.flex-1.flex-grow.flex-col.items-center > div > div.max-w-7xl > div.flex.gap-16.flex-col.w-full > div:nth-child(3) > div > div > div.p-16.box-border.flex.flex-col.gap-8.shrink-0.basis-\\[37\\%\\].relative > div.flex.gap-16.mb-8 > div.flex.flex-col.w-full > div.flex.justify-between > div > span"
        )
        info['distance'] = distance_element.text.strip()
    except NoSuchElementException:
        try:
            distance_element = doctor_element.find_element(
                By.CSS_SELECTOR, 
                "svg[data-test-id='location-arrow-icon'] + span"
            )
            info['distance'] = distance_element.text.strip()
        except NoSuchElementException:
            try:
                distance_spans = doctor_element.find_elements(By.CSS_SELECTOR, "span")
                for span in distance_spans:
                    text = span.text.strip()
                    if re.search(r'\d+(\.\d+)?\s*(km|m)\b', text.lower()):
                        info['distance'] = text
                        break
                if 'distance' not in info:
                    all_text = doctor_element.text
                    distance_match = re.search(r'(\d+(\.\d+)?\s*(km|m))', all_text, re.IGNORECASE)
                    if distance_match:
                        info['distance'] = distance_match.group(1)
                    else:
                        info['distance'] = "Distance non trouvée"
            except Exception:
                info['distance'] = "Distance non trouvée"

    # --- CONVENTIONNEMENT ---
    try:
        # Chercher l'icône euro et le texte suivant
        euro_icon = doctor_element.find_element(By.CSS_SELECTOR, "svg[data-icon-name='regular/euro-sign']")
        parent = euro_icon.find_element(By.XPATH, "../..")
        conventionnement_element = parent.find_element(By.CSS_SELECTOR, "p")
        info['conventionnement'] = conventionnement_element.text.strip()
    except NoSuchElementException:
        try:
            # Fallback: chercher directement le texte "Conventionné"
            conv_elements = doctor_element.find_elements(By.XPATH, ".//*[contains(text(), 'Conventionné') or contains(text(), 'conventionné')]")
            if conv_elements:
                info['conventionnement'] = conv_elements[0].text.strip()
            else:
                info['conventionnement'] = "Non spécifié"
        except:
            info['conventionnement'] = "Non spécifié"

    return info

def get_available_slots(driver, max_slots=10):
    available_slots = []
    time.sleep(args.delay)

    # --- Sélecteurs à jour pour les créneaux après expansion ---
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
            print(f"Créneaux trouvés avec sélecteur: {selector} ({len(slots)} créneaux)")
            break

    # --- Extraire le texte des créneaux ---
    for i, slot in enumerate(slots[:max_slots]):
        try:
            slot_text = slot.text.strip()
            if slot_text and len(slot_text) > 1:
                cleaned_text = re.sub(r'\s+', ' ', slot_text)
                if re.search(r'\d{1,2}[h:]\d{0,2}', cleaned_text) or re.search(r'\b\d{1,2}:\d{2}\b', cleaned_text):
                    if cleaned_text not in available_slots:
                        available_slots.append(cleaned_text)
        except Exception as e:
            print(f"Erreur lors de l'extraction du créneau {i}: {e}")
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
driver = webdriver.Chrome(service=service, options=options)
driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
wait = WebDriverWait(driver, 15)
filters = parse_filters(args.filters)

try:
    driver.get("https://www.doctolib.fr/")

    # --- Rejeter cookies si présent ---
    try:
        reject_btn = wait.until(
            EC.element_to_be_clickable((By.ID, "didomi-notice-disagree-button"))
        )
        reject_btn.click()
    except TimeoutException:
        pass

    # --- Effectuer la recherche ---
    try:
        search_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input.searchbar-query-input"))
        )
        search_input.clear()
        search_input.send_keys(args.query)
        time.sleep(1)

        place_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input.searchbar-place-input"))
        )
        place_input.clear()
        place_input.send_keys(args.location)
        time.sleep(1)

        submit_btn = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.searchbar-submit-button"))
        )
        submit_btn.click()
        time.sleep(5)
    except Exception:
        print("Erreur lors de la recherche")
        driver.quit()
        exit()

    try:
            last_height = driver.execute_script("return document.body.scrollHeight")
            while True:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)  # laisser le temps au JS de charger
                
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height

            print("Scroll terminé, tous les résultats devraient être visibles.")
    except Exception as e:
        print(f"Erreur pendant le scroll: {e}")

    # --- Récupérer les médecins ---
    doctors_data = []
    result_selectors = [
        ".dl-search-result",
        ".search-result",
        ".doctor-card",
        ".practitioner-card",
        ".result-item",
        "[data-test-id*='result']",
        "[data-testid*='result']",
        "[class*='result']"
    ]

    doctors = []
    for selector in result_selectors:
        doctors = driver.find_elements(By.CSS_SELECTOR, selector)
        if doctors:
            print(f"Trouvé {len(doctors)} médecins avec le sélecteur: {selector}")
            break

    if not doctors:
        all_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/doctors/'], a[href*='/medecins/']")
        if all_links:
            doctors = all_links[:args.max_results]
            print(f"Trouvé {len(doctors)} médecins via les liens")
        else:
            print("Aucun médecin trouvé")
            driver.quit()
            exit()

    # --- Appliquer les filtres ---
    if filters and doctors:
        if 'disponibilites' in filters:
            try:
                availability_filter_selectors = [
                    "button[data-test-id='availability-filter']",
                    ".filter-availability",
                    "button:contains('Disponibilités')",
                    ".availability-filter",
                    "button[data-testid='availability-filter']",
                    "[class*='availability']"
                ]
                for selector in availability_filter_selectors:
                    try:
                        dispo_filter = driver.find_element(By.CSS_SELECTOR, selector)
                        dispo_filter.click()
                        time.sleep(3)
                        print("Filtre disponibilités appliqué")
                        break
                    except NoSuchElementException:
                        continue
            except Exception as e:
                print(f"Erreur lors de l'application du filtre disponibilités : {e}")

    # --- Analyser chaque médecin ---
    doctors_to_process = doctors[:args.max_results]
    print(f"Analyse de {len(doctors_to_process)} médecins...")
    
    for i, doctor in enumerate(doctors_to_process, 1):
        print(f"Traitement du médecin {i}/{len(doctors_to_process)}")
        try:
            doctor_info = extract_doctor_info_from_list(doctor)
            doctor_info['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            try:
                # Faire défiler jusqu'au médecin
                driver.execute_script("arguments[0].scrollIntoView(true);", doctor)
                time.sleep(1)
                doctor.click()
                time.sleep(args.delay)

                try:
                    voir_plus_btn = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.XPATH, "//*[@id='main-content']/div[2]/div/div[1]/div[2]/div[1]/div/div/div[2]/div/div[2]/div[2]/button/span"))
                    )
                    driver.execute_script("arguments[0].click();", voir_plus_btn)
                    print("Bouton 'Voir plus de créneaux' cliqué via JS")
                    time.sleep(2)
                except TimeoutException:
                    print("Bouton 'Voir plus de créneaux' non trouvé")
                except Exception as e:
                    print(f"Erreur lors du clic sur 'Voir plus de créneaux': {e}")

                # --- Récupération des créneaux ---
                available_slots = get_available_slots(driver)
                doctor_info['creneaux_disponibles'] = '; '.join(available_slots) if available_slots else "Aucun créneau visible"
                doctor_info['nb_creneaux'] = len(available_slots)

                driver.back()
                time.sleep(args.delay)

            except Exception as e:
                print(f"Erreur lors du traitement des créneaux pour le médecin {i}: {e}")
                doctor_info['creneaux_disponibles'] = "Erreur de consultation"
                doctor_info['nb_creneaux'] = -1

            doctors_data.append(doctor_info)
            print(f"Médecin {i} traité: {doctor_info.get('nom', 'Nom inconnu')}")
            
        except Exception as e:
            print(f"Erreur lors du traitement du médecin {i}: {e}")
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