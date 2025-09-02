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

    # --- ADRESSE --- (Version corrigée)
# --- ADRESSE --- (Version corrigée pour éviter la récupération multiple)
    try:
        address_parts = []
        
        # D'abord, essayer de trouver le conteneur d'adresse spécifique à cette carte
        try:
            # Chercher le conteneur d'adresse dans cette carte spécifique
            address_container = doctor_element.find_element(
                By.CSS_SELECTOR, 
                "div.flex.flex-wrap.gap-x-4"
            )
            # Récupérer SEULEMENT les paragraphes d'adresse de ce conteneur
            address_elements = address_container.find_elements(
                By.CSS_SELECTOR, 
                "p.XZWvFVZmM9FHf461kjNO.G5dSlmEET4Zf5bQ5PR69"
            )
            
            for elem in address_elements:
                text = elem.text.strip()
                if text and text not in address_parts:  # Éviter les doublons
                    address_parts.append(text)
            
            # Limiter à 2 parties maximum (généralement rue + ville)
            if len(address_parts) > 2:
                address_parts = address_parts[:2]
                
        except NoSuchElementException:
            # Fallback: chercher dans les éléments directs de cette carte seulement
            try:
                # Chercher les paragraphes d'adresse directement dans cette carte
                address_paragraphs = doctor_element.find_elements(
                    By.CSS_SELECTOR, 
                    "p.XZWvFVZmM9FHf461kjNO.G5dSlmEET4Zf5bQ5PR69"
                )
                
                for p in address_paragraphs:
                    text = p.text.strip()
                    # Identifier les parties d'adresse avec des critères plus stricts
                    if text and (
                        any(word in text.lower() for word in ['rue', 'avenue', 'boulevard', 'place', 'allée', 'chemin', 'impasse']) or
                        re.search(r'\b\d{5}\s+[A-Za-zÀ-ÿ\-\s]+\b', text)  # Code postal + ville
                    ):
                        if text not in address_parts:  # Éviter les doublons
                            address_parts.append(text)
                
                # Limiter à 2 parties maximum
                if len(address_parts) > 2:
                    address_parts = address_parts[:2]
                    
            except Exception:
                pass
        
        # Si on a trouvé des adresses, les joindre
        if address_parts:
            info['adresse'] = ", ".join(address_parts)
        else:
            # Dernier fallback: chercher près de l'icône de localisation
            try:
                # Chercher l'icône d'adresse spécifique à cette carte
                location_icon = doctor_element.find_element(
                    By.CSS_SELECTOR, 
                    "svg[data-test-id='healthcare-provider-icon'][aria-label='Adresse']"
                )
                # Remonter au parent et chercher les paragraphes suivants
                parent = location_icon.find_element(By.XPATH, "../..")
                address_elements = parent.find_elements(By.CSS_SELECTOR, "p")
                
                address_texts = []
                for elem in address_elements:
                    text = elem.text.strip()
                    if text and len(text) > 3:  # Éviter les textes trop courts
                        address_texts.append(text)
                
                # Prendre seulement les 2 premiers éléments pertinents
                relevant_addresses = []
                for text in address_texts:
                    if any(word in text.lower() for word in ['rue', 'avenue', 'boulevard', 'place']) or \
                    re.search(r'\d{5}', text):
                        relevant_addresses.append(text)
                        if len(relevant_addresses) >= 2:  # Limiter à 2 parties
                            break
                
                info['adresse'] = ", ".join(relevant_addresses) if relevant_addresses else "Adresse non trouvée"
                
            except NoSuchElementException:
                info['adresse'] = "Adresse non trouvée"
                    
    except Exception as e:
        print(f"Erreur lors de l'extraction de l'adresse: {e}")
        info['adresse'] = "Erreur d'extraction"

    # --- DISTANCE --- (Updated version)
    try:
        # Try the specific selector you provided first
        distance_element = doctor_element.find_element(
            By.CSS_SELECTOR, 
            "#main-content > div.flex.flex-1.flex-grow.flex-col.items-center > div > div.max-w-7xl > div.flex.gap-16.flex-col.w-full > div:nth-child(3) > div > div > div.p-16.box-border.flex.flex-col.gap-8.shrink-0.basis-\\[37\\%\\].relative > div.flex.gap-16.mb-8 > div.flex.flex-col.w-full > div.flex.justify-between > div > span"
        )
        info['distance'] = distance_element.text.strip()
    except NoSuchElementException:
        try:
            # Original selector with location-arrow icon
            distance_element = doctor_element.find_element(
                By.CSS_SELECTOR, 
                "svg[data-test-id='location-arrow-icon'] + span"
            )
            info['distance'] = distance_element.text.strip()
        except NoSuchElementException:
            try:
                # Look for spans containing "km" or "m" (meters)
                distance_spans = doctor_element.find_elements(By.CSS_SELECTOR, "span")
                for span in distance_spans:
                    text = span.text.strip()
                    if re.search(r'\d+(\.\d+)?\s*(km|m)\b', text.lower()):
                        info['distance'] = text
                        break
                if 'distance' not in info:
                    # Try looking for any text that matches distance patterns
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

    # --- ÉTAPE 1: Cliquer sur le bouton pour agrandir la liste des créneaux ---
    try:
        # Sélecteur spécifique que vous avez fourni
        expand_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 
                "#main-content > div.flex.flex-1.flex-grow.flex-col.items-center > div > div.max-w-7xl > div.flex.gap-16.flex-col.w-full > div:nth-child(1) > div > div > div.m-16.basis-\\[63\\%\\].min-h-240 > div > div.flex.flex-col.gap-16.w-full.items-center > div:nth-child(2) > button > span"
            ))
        )
        driver.execute_script("arguments[0].scrollIntoView(true);", expand_button)
        time.sleep(1)
        expand_button.click()
        print("Bouton d'expansion des créneaux cliqué")
        time.sleep(2)  # Attendre que la liste s'agrandisse
        
    except TimeoutException:
        # Fallbacks pour différents sélecteurs de boutons d'expansion
        expand_selectors = [
            "button[aria-label*='Voir plus']",
            "button[aria-label*='Afficher plus']",
            "button:contains('Voir plus')",
            "button:contains('Afficher plus')",
            ".expand-slots",
            ".show-more-slots",
            "button[data-test-id*='expand']",
            "button[data-test-id*='show-more']",
            ".calendar-expand",
            "button.expand",
            "span:contains('Voir plus')",
            "span:contains('Afficher plus')"
        ]
        
        button_found = False
        for selector in expand_selectors:
            try:
                expand_btn = driver.find_element(By.CSS_SELECTOR, selector)
                if expand_btn.is_displayed() and expand_btn.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView(true);", expand_btn)
                    time.sleep(1)
                    expand_btn.click()
                    print(f"Bouton d'expansion trouvé avec sélecteur: {selector}")
                    time.sleep(2)
                    button_found = True
                    break
            except NoSuchElementException:
                continue
                
        if not button_found:
            print("Aucun bouton d'expansion trouvé, utilisation de la vue par défaut")
    
    except Exception as e:
        print(f"Erreur lors du clic sur le bouton d'expansion: {e}")

    # --- ÉTAPE 2: Récupérer les créneaux après expansion ---
    # Sélecteurs pour les créneaux (ordre de priorité)
    slot_selectors = [
        "[data-test-id='booking-slot']",
        ".availabilities-slot",
        ".booking-slot",
        ".dl-booking-slot",
        ".available-slot",
        ".slot",
        "button[data-test-id*='slot']",
        ".calendar-slot",
        ".time-slot",
        ".appointment-slot",
        ".slot-time",
        "[data-testid*='slot']",
        "[class*='slot']",
        "button[aria-label*='heure']",
        "button[aria-label*='créneau']"
    ]

    slots = []
    for selector in slot_selectors:
        slots = driver.find_elements(By.CSS_SELECTOR, selector)
        if slots:
            print(f"Créneaux trouvés avec sélecteur: {selector} ({len(slots)} créneaux)")
            break

    # Si aucun créneau direct, chercher les calendriers/dates
    if not slots:
        print("Recherche de dates disponibles...")
        date_selectors = [
            ".available-date",
            ".calendar-day:not(.disabled)",
            ".booking-date",
            "[data-test-id*='date']:not([disabled])",
            ".calendar-date.available",
            "[class*='date']:not(.disabled)",
            "button[aria-label*='jour']"
        ]
        
        for selector in date_selectors:
            dates = driver.find_elements(By.CSS_SELECTOR, selector)
            if dates:
                print(f"Dates trouvées avec sélecteur: {selector} ({len(dates)} dates)")
                for date in dates[:3]:  # Limiter à 3 dates
                    try:
                        driver.execute_script("arguments[0].scrollIntoView(true);", date)
                        time.sleep(1)
                        date.click()
                        time.sleep(2)
                        
                        # Chercher les créneaux pour cette date
                        for slot_selector in slot_selectors:
                            day_slots = driver.find_elements(By.CSS_SELECTOR, slot_selector)
                            if day_slots:
                                slots.extend(day_slots[:5])
                                print(f"Ajout de {len(day_slots[:5])} créneaux pour cette date")
                                break
                    except Exception as e:
                        print(f"Erreur lors du clic sur la date: {e}")
                        continue
                break

    # --- ÉTAPE 3: Extraire le texte des créneaux ---
    for i, slot in enumerate(slots[:max_slots]):
        try:
            slot_text = slot.text.strip()
            if slot_text and len(slot_text) > 1:
                # Nettoyer le texte du créneau
                cleaned_text = re.sub(r'\s+', ' ', slot_text)
                # Filtrer les textes qui ne ressemblent pas à des heures
                if re.search(r'\d{1,2}[h:]\d{0,2}', cleaned_text) or \
                   re.search(r'\b\d{1,2}:\d{2}\b', cleaned_text):
                    if cleaned_text not in available_slots:
                        available_slots.append(cleaned_text)
        except Exception as e:
            print(f"Erreur lors de l'extraction du créneau {i}: {e}")
            continue

    # --- ÉTAPE 4: Si toujours rien, chercher des indicateurs de disponibilité ---
    if not available_slots:
        print("Recherche d'indicateurs de disponibilité...")
        availability_indicators = [
            ".next-available-slot",
            ".earliest-slot",
            ".disponibilite",
            ".availability-info",
            ".next-appointment",
            "[class*='available']",
            "[data-test-id*='availability']"
        ]
        
        for selector in availability_indicators:
            try:
                indicator = driver.find_element(By.CSS_SELECTOR, selector)
                text = indicator.text.strip()
                if text and len(text) > 3:
                    available_slots.append(f"Prochaine dispo: {text}")
                    break
            except NoSuchElementException:
                continue

    # Si encore rien, indiquer qu'aucun créneau n'est visible
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
                driver.execute_script("arguments[0].scrollIntoView(true);", doctor)
                time.sleep(1)
                doctor.click()
                time.sleep(args.delay)

                available_slots = get_available_slots(driver)
                doctor_info['creneaux_disponibles'] = '; '.join(available_slots) if available_slots else "Aucun créneau visible"
                doctor_info['nb_creneaux'] = len(available_slots)

                driver.back()
                time.sleep(args.delay)
            except Exception as e:
                print(f"Erreur lors de la consultation des créneaux pour le médecin {i}: {e}")
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