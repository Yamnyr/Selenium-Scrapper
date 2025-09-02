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

def extract_doctor_info_from_list(doctor_element):
    """Extrait les infos principales d'un médecin depuis la liste de résultats"""
    info = {}
    try:
        name_element = doctor_element.find_element(By.CSS_SELECTOR, "h2")
        info['nom'] = name_element.text.strip()
    except NoSuchElementException:
        info['nom'] = "Nom non trouvé"

    try:
        specialty_element = doctor_element.find_element(By.CSS_SELECTOR, "p[data-design-system-component='Paragraph']")
        specialty_text = specialty_element.text.strip()
        if specialty_text and not any(word in specialty_text.lower() for word in ['rue', 'avenue', 'boulevard', 'km', 'conventionné']):
            info['specialite'] = specialty_text
        else:
            info['specialite'] = args.query
    except NoSuchElementException:
        info['specialite'] = args.query

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

    try:
        distance_element = doctor_element.find_element(By.XPATH, ".//*[contains(text(), 'km') or contains(text(), 'm')]")
        info['distance'] = distance_element.text.strip()
    except NoSuchElementException:
        info['distance'] = "Distance non trouvée"

    try:
        conv_elements = doctor_element.find_elements(By.XPATH, ".//*[contains(text(), 'Conventionné') or contains(text(), 'conventionné')]")
        info['conventionnement'] = conv_elements[0].text.strip() if conv_elements else "Non spécifié"
    except Exception:
        info['conventionnement'] = "Non spécifié"

    try:
        rating_element = doctor_element.find_element(By.CSS_SELECTOR, "[data-test-id='review-summary-rating']")
        info['note'] = rating_element.text.strip()
    except NoSuchElementException:
        info['note'] = "Non renseignée"

    try:
        # Essayer plusieurs sélecteurs pour le lien de profil - basé sur votre exemple HTML
        link_selectors = [
            "a[href*='/infirmier/'] h2",  # Lien contenant h2 pour infirmiers
            "a[href*='/medecin/'] h2",    # Lien contenant h2 pour médecins
            "a[href*='/doctor/'] h2",     # Lien contenant h2 pour doctors
            "h2[data-design-system-component='Text']",  # H2 avec cet attribut spécifique
            "a[href*='/infirmier/']",     # Directement le lien infirmier
            "a[href*='/medecin/']",       # Directement le lien médecin  
            "a[href*='/doctor/']",        # Directement le lien doctor
            "a[href*='/dentiste/']",      # Lien dentiste
            "a[href*='/kinesitherapeute/']", # Lien kiné
            "a[href*='/psychologue/']"    # Lien psy
        ]
        
        link_found = False
        for selector in link_selectors:
            try:
                if " h2" in selector:
                    # Si le sélecteur vise un h2 dans un lien, on récupère le parent
                    h2_element = doctor_element.find_element(By.CSS_SELECTOR, selector)
                    link_element = h2_element.find_element(By.XPATH, "./parent::a")
                else:
                    # Sinon on cherche directement le lien
                    link_element = doctor_element.find_element(By.CSS_SELECTOR, selector)
                
                href = link_element.get_attribute("href")
                if href and ('/' in href):
                    info['lien_profil'] = href
                    link_found = True
                    break
            except (NoSuchElementException, Exception):
                continue
        
        if not link_found:
            info['lien_profil'] = "Lien non trouvé"
                
    except Exception as e:
        info['lien_profil'] = "Erreur d'extraction"

    return info

def extract_profile_details(driver, wait, doctor_info):
    """Extrait les détails depuis le profil du médecin avec logs détaillés"""
    print(f"🔍 Extraction des détails du profil pour: {doctor_info.get('nom', 'Nom inconnu')}")
    
    # Initialiser les valeurs par défaut
    doctor_info['tarifs_remboursement'] = "Non renseigné"
    doctor_info['moyens_paiement'] = "Non renseigné"
    doctor_info['expertises_actes'] = "Non renseigné"
    
    try:
        # Attendre que la page soit complètement chargée
        print("⏳ Attente du chargement complet de la page...")
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "main")))
        time.sleep(3)
        
        # Log de l'URL actuelle
        current_url = driver.current_url
        print(f"📍 URL du profil: {current_url}")
        
        # Scroll pour s'assurer que tout le contenu est chargé
        print("📜 Scroll de la page du profil...")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)
        
        # Recherche des tarifs - Plusieurs sélecteurs possibles
        print("💰 Recherche des informations de tarifs...")
        tarif_selectors = [
            "#payment_means .dl-profile-text",
            "[data-test-id*='tarif']",
            "[data-test-id*='price']",
            ".dl-profile-card-content:contains('Tarif')",
            ".profile-payment-info",
            "*[contains(text(), 'Secteur')]",
            "*[contains(text(), 'Tarif')]",
            "*[contains(text(), '€')]"
        ]
        
        tarif_found = False
        for selector in tarif_selectors:
            try:
                if "contains" in selector:
                    # Utiliser XPath pour les sélecteurs avec contains
                    text_to_find = selector.split("contains(text(), '")[1].split("'")[0]
                    elements = driver.find_elements(By.XPATH, xpath_selector)
                else:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                
                if elements:
                    tarif_text = elements[0].text.strip()
                    if tarif_text and len(tarif_text) > 5:  # Éviter les textes trop courts
                        doctor_info['tarifs_remboursement'] = tarif_text
                        print(f"✅ Tarifs trouvés avec '{selector}': {tarif_text[:50]}...")
                        tarif_found = True
                        break
            except Exception as e:
                print(f"❌ Erreur avec sélecteur '{selector}': {e}")
                continue
        
        if not tarif_found:
            print("❌ Aucun tarif trouvé")
        
        # Recherche des moyens de paiement
        print("💳 Recherche des moyens de paiement...")
        payment_selectors = [
            "#payment_means ~ div .dl-profile-text",
            "[data-test-id*='payment']",
            ".payment-methods",
            "*[contains(text(), 'Carte bancaire')]",
            "*[contains(text(), 'Espèces')]",
            "*[contains(text(), 'Chèque')]"
        ]
        
        payment_found = False
        for selector in payment_selectors:
            try:
                if "contains" in selector:
                    text_to_find = selector.split("contains(text(), '")[1].split("'")[0]
                    elements = driver.find_elements(By.XPATH, xpath_selector)
                else:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                
                if elements:
                    payment_text = elements[0].text.strip()
                    if payment_text and len(payment_text) > 3:
                        doctor_info['moyens_paiement'] = payment_text
                        print(f"✅ Moyens de paiement trouvés avec '{selector}': {payment_text[:50]}...")
                        payment_found = True
                        break
            except Exception as e:
                print(f"❌ Erreur avec sélecteur '{selector}': {e}")
                continue
        
        if not payment_found:
            print("❌ Aucun moyen de paiement trouvé")
        
        # Recherche des expertises et actes
        print("🎯 Recherche des expertises et actes...")
        skills_selectors = [
            "#skills .dl-profile-skills .dl-profile-skill-chip",
            "[data-test-id*='skill']",
            ".skill-chip",
            ".expertise-list",
            ".specialties",
            ".dl-profile-skill-chip"
        ]
        
        skills_found = False
        for selector in skills_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    skills = [elem.text.strip() for elem in elements if elem.text.strip()]
                    if skills:
                        doctor_info['expertises_actes'] = "; ".join(skills)
                        print(f"✅ Expertises trouvées avec '{selector}': {len(skills)} compétences")
                        skills_found = True
                        break
            except Exception as e:
                print(f"❌ Erreur avec sélecteur '{selector}': {e}")
                continue
        
        if not skills_found:
            print("❌ Aucune expertise trouvée")
        
        # Log final des données extraites du profil
        print("📊 Résumé des données extraites du profil:")
        print(f"   - Tarifs: {doctor_info['tarifs_remboursement'][:50]}...")
        print(f"   - Paiements: {doctor_info['moyens_paiement'][:50]}...")
        print(f"   - Expertises: {doctor_info['expertises_actes'][:50]}...")
        
    except Exception as e:
        print(f"❌ Erreur générale lors de l'extraction du profil: {e}")
        print(f"   Type d'erreur: {type(e).__name__}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")

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

    print("🌐 Accès à Doctolib...")
    driver.get("https://www.doctolib.fr/")

    # --- Rejeter cookies si présent ---
    try:
        print("🍪 Tentative de rejet des cookies...")
        reject_btn = wait.until(EC.element_to_be_clickable((By.ID, "didomi-notice-disagree-button")))
        reject_btn.click()
        print("✅ Cookies rejetés")
    except TimeoutException:
        print("ℹ️ Pas de bannière de cookies détectée")

    # --- Recherche ---
    try:
        print(f"🔍 Recherche: '{args.query}' à '{args.location}'")
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
        print("✅ Recherche lancée")
        time.sleep(5)
    except Exception as e:
        print(f"❌ Erreur lors de la recherche: {e}")
        driver.quit()
        exit()

    # --- Scroll pour charger tous les résultats ---
    try:
        print("📜 Chargement de tous les résultats...")
        last_height = driver.execute_script("return document.body.scrollHeight")
        scroll_count = 0
        while True:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            new_height = driver.execute_script("return document.body.scrollHeight")
            scroll_count += 1
            if new_height == last_height or scroll_count > 10:  # Limite pour éviter les boucles infinies
                break
            last_height = new_height
        print(f"✅ Scroll terminé après {scroll_count} tentatives")
    except Exception as e:
        print(f"❌ Erreur pendant le scroll: {e}")

    # --- Récupérer toutes les cartes des médecins ---
    print("👥 Recherche des cartes de médecins...")
    
    # Essayer plusieurs sélecteurs pour les cartes de médecins
    card_selectors = [
        "#main-content div[data-test-id*='practitioner-card']",
        "#main-content div[data-test-id*='doctor-card']", 
        "#main-content .search-result",
        "#main-content .practitioner-result",
        "#main-content > div.flex.flex-1.flex-grow.flex-col.items-center > div > div.max-w-7xl > div.flex.gap-16.flex-col.w-full > div",
        ".search-result-card",
        "[data-test-id*='doctor-card']",
        ".doctor-card",
        ".practitioner-card"
    ]
    
    doctor_cards = []
    for selector in card_selectors:
        try:
            cards = driver.find_elements(By.CSS_SELECTOR, selector)
            if cards:
                # Filtrer les cartes qui contiennent vraiment des médecins/infirmiers
                filtered_cards = []
                for card in cards:
                    try:
                        card_text = card.text.lower()
                        # Vérifier si c'est bien une carte de praticien
                        if any(keyword in card_text for keyword in ['dr ', 'docteur', 'infirmier', 'mme ', 'mr ', 'm. ', 'mlle']):
                            filtered_cards.append(card)
                    except:
                        filtered_cards.append(card)  # En cas de doute, on garde
                
                if filtered_cards:
                    print(f"✅ {len(filtered_cards)} cartes valides trouvées avec le sélecteur: {selector}")
                    print(f"   ({len(cards)} cartes totales avant filtrage)")
                    doctor_cards = filtered_cards
                    break
            else:
                print(f"❌ Aucune carte avec sélecteur: {selector}")
        except Exception as e:
            print(f"❌ Erreur avec sélecteur '{selector}': {e}")
    
    if not doctor_cards:
        print("❌ Aucune carte de médecin trouvée - Diagnostic:")
        try:
            # Debug: afficher le contenu de la page
            page_source_sample = driver.page_source[:1000]
            print(f"🔍 Début du source HTML: {page_source_sample}...")
            
            # Rechercher tous les éléments contenant "Dr" ou "Docteur"
            dr_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Dr ') or contains(text(), 'Docteur') or contains(text(), 'Mme ') or contains(text(), 'M. ')]")
            print(f"🔍 {len(dr_elements)} éléments contenant des titres de médecins trouvés")
            
        except Exception as debug_e:
            print(f"❌ Erreur de diagnostic: {debug_e}")
        
        driver.quit()
        exit()

    doctors_count = min(len(doctor_cards), args.max_results)
    print(f"📋 Analyse de {doctors_count} médecins sur {len(doctor_cards)} trouvés...")

    for i, card in enumerate(doctor_cards[:doctors_count], 1):
        print(f"\n{'='*50}")
        print(f"👨‍⚕️ Médecin {i}/{doctors_count}")
        print(f"{'='*50}")
        
        doctor_info = extract_doctor_info_from_list(card)
        doctor_info['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Debug : afficher les informations de base extraites
        print(f"📋 Infos extraites: {doctor_info.get('nom', 'Nom inconnu')}")
        print(f"   🔗 Lien profil: {doctor_info.get('lien_profil', 'Non défini')}")

        # Si pas de lien trouvé, faire un diagnostic approfondi
        if doctor_info.get('lien_profil') in ["Lien non trouvé", "Erreur d'extraction"]:
            print("🔍 DIAGNOSTIC APPROFONDI - Recherche manuelle du lien:")
            try:
                # Afficher la structure HTML de la carte (échantillon)
                card_html = card.get_attribute('outerHTML')
                print(f"   📄 HTML de la carte (500 premiers chars): {card_html[:500]}...")
                
                # Chercher TOUS les liens dans la carte
                all_links = card.find_elements(By.TAG_NAME, "a")
                print(f"   🔗 {len(all_links)} liens trouvés dans cette carte:")
                
                for idx, link in enumerate(all_links):
                    try:
                        href = link.get_attribute("href") or "Pas de href"
                        text = link.text.strip() or "Pas de texte" 
                        classes = link.get_attribute("class") or "Pas de classe"
                        onclick = link.get_attribute("onclick") or "Pas d'onclick"
                        print(f"      🔗 Link {idx+1}:")
                        print(f"         href: {href}")
                        print(f"         text: {text[:50]}...")
                        print(f"         class: {classes[:80]}...")
                        if onclick != "Pas d'onclick":
                            print(f"         onclick: {onclick[:50]}...")
                        
                        # Si c'est un lien vers un profil, l'utiliser
                        if href != "Pas de href" and any(path in href for path in ['/infirmier/', '/medecin/', '/doctor/', '/dentiste/', '/kinesitherapeute/', '/psychologue/']):
                            doctor_info['lien_profil'] = href
                            print(f"   ✅ LIEN DE PROFIL TROUVÉ MANUELLEMENT: {href}")
                            break
                            
                    except Exception as link_e:
                        print(f"      ❌ Erreur analyse link {idx+1}: {link_e}")
                        
            except Exception as diag_e:
                print(f"   ❌ Erreur diagnostic: {diag_e}")

        # --- Cliquer sur tous les boutons "Voir plus" dans la carte ---
        try:
            voir_plus_count = 0
            while True:
                try:
                    voir_plus_btn = card.find_element(By.XPATH, ".//button[contains(., 'Voir plus')]")
                    driver.execute_script("arguments[0].click();", voir_plus_btn)
                    voir_plus_count += 1
                    print(f"👁️ Bouton 'Voir plus' #{voir_plus_count} cliqué")
                    time.sleep(1)
                except NoSuchElementException:
                    if voir_plus_count > 0:
                        print(f"✅ {voir_plus_count} boutons 'Voir plus' traités")
                    break
        except Exception as e:
            print(f"❌ Erreur clic 'Voir plus': {e}")

        # --- Récupération des créneaux ---
        try:
            print("📅 Recherche des créneaux disponibles...")
            available_slots = []
            slot_selectors = [
                "button[data-test-id*='slot']",
                ".dl-booking-slot",
                ".available-slot",
                ".slot-time",
                ".calendar-slot",
                "button[data-test-id*='time-slot']"
            ]
            
            for selector in slot_selectors:
                try:
                    slot_elements = card.find_elements(By.CSS_SELECTOR, selector)
                    if slot_elements:
                        print(f"🎯 {len(slot_elements)} créneaux trouvés avec '{selector}'")
                        for slot in slot_elements:
                            slot_text = slot.text.strip()
                            if slot_text and slot_text not in available_slots and re.match(r'\d{1,2}[h:]\d{2}', slot_text):
                                available_slots.append(slot_text)
                        break
                except Exception as e:
                    print(f"❌ Erreur avec sélecteur de créneaux '{selector}': {e}")
                    continue
            
            doctor_info['creneaux_disponibles'] = '; '.join(available_slots) if available_slots else "Aucun créneau"
            doctor_info['nb_creneaux'] = len(available_slots)
            print(f"📊 {len(available_slots)} créneaux récupérés")
        except Exception as e:
            print(f"❌ Erreur récupération créneaux: {e}")
            doctor_info['creneaux_disponibles'] = "Erreur"
            doctor_info['nb_creneaux'] = -1

        # --- Aller sur le profil du médecin pour récupérer les détails ---
        profile_link = doctor_info.get('lien_profil')
        if profile_link and profile_link != "Lien non trouvé" and profile_link != "Erreur d'extraction":
            print(f"🔗 Ouverture du profil: {profile_link}")
            try:
                # Ouvrir le profil dans un nouvel onglet
                original_window = driver.current_window_handle
                driver.execute_script("window.open(arguments[0], '_blank');", profile_link)
                
                # Attendre que le nouvel onglet soit disponible
                wait.until(lambda driver: len(driver.window_handles) > 1)
                
                # Basculer vers le nouvel onglet
                new_window = [window for window in driver.window_handles if window != original_window][0]
                driver.switch_to.window(new_window)
                print("✅ Basculement vers l'onglet du profil réussi")
                
                # Extraire les détails du profil
                extract_profile_details(driver, wait, doctor_info)
                
                # Fermer l'onglet du profil et revenir à la liste
                print("🔄 Retour à la liste des résultats...")
                driver.close()
                driver.switch_to.window(original_window)
                
                # Attendre un peu pour éviter les problèmes de timing
                time.sleep(args.delay)
                
            except Exception as e:
                print(f"❌ Erreur lors de l'accès au profil: {e}")
                print(f"   Type d'erreur: {type(e).__name__}")
                
                # S'assurer qu'on revient à la fenêtre principale en cas d'erreur
                try:
                    if len(driver.window_handles) > 1:
                        driver.close()
                    driver.switch_to.window(original_window)
                except:
                    pass
        else:
            print(f"❌ Pas de profil accessible pour {doctor_info.get('nom', 'Nom inconnu')}")
            if profile_link:
                print(f"   Lien trouvé: {profile_link}")
            
            # Debug additionnel : essayer de trouver tous les liens dans la carte
            print("🔍 DEBUG - Recherche de tous les liens dans cette carte:")
            try:
                all_links_in_card = card.find_elements(By.TAG_NAME, "a")
                print(f"   {len(all_links_in_card)} liens totaux dans la carte:")
                for idx, link in enumerate(all_links_in_card[:5]):  # Afficher les 5 premiers
                    href = link.get_attribute("href") or "Pas de href"
                    text = link.text.strip() or "Pas de texte"
                    classes = link.get_attribute("class") or "Pas de classe"
                    print(f"      Link {idx+1}: href='{href}' | text='{text[:30]}...' | class='{classes[:50]}...'")
            except Exception as debug_e:
                print(f"   ❌ Erreur debug liens: {debug_e}")

        doctors_data.append(doctor_info)
        print(f"✅ Médecin traité: {doctor_info.get('nom', 'Nom inconnu')}")
        print(f"📊 Données collectées:")
        for key, value in doctor_info.items():
            if key != 'timestamp':
                print(f"   - {key}: {str(value)[:60]}{'...' if len(str(value)) > 60 else ''}")

finally:
    print(f"\n{'='*60}")
    print("💾 SAUVEGARDE DES RÉSULTATS")
    print(f"{'='*60}")
    
    if doctors_data:
        # --- Sauvegarde CSV ---
        csv_filename = args.output
        with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = [
                'nom', 'specialite', 'adresse', 'note', 'distance',
                'lien_profil', 'conventionnement', 'creneaux_disponibles',
                'nb_creneaux', 'tarifs_remboursement', 'moyens_paiement', 'expertises_actes',
                'timestamp'
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for doctor in doctors_data:
                writer.writerow(doctor)
        print(f"✅ Résultats CSV sauvegardés dans {csv_filename} ({len(doctors_data)} médecins)")

        # --- Sauvegarde JSON ---
        if args.json_output:
            json_filename = args.output.replace('.csv', '.json')
            with open(json_filename, 'w', encoding='utf-8') as jsonfile:
                json.dump(doctors_data, jsonfile, ensure_ascii=False, indent=2)
            print(f"✅ Résultats JSON sauvegardés dans {json_filename}")
        
        # Afficher un résumé
        print(f"\n📈 RÉSUMÉ:")
        print(f"   - {len(doctors_data)} médecins traités")
        profiles_with_details = sum(1 for d in doctors_data if d.get('tarifs_remboursement', 'Non renseigné') != 'Non renseigné')
        print(f"   - {profiles_with_details} profils avec détails récupérés")
        slots_total = sum(d.get('nb_creneaux', 0) for d in doctors_data if d.get('nb_creneaux', 0) > 0)
        print(f"   - {slots_total} créneaux au total trouvés")
    else:
        print("❌ Aucune donnée collectée")

    print("🔚 Fermeture du navigateur...")
    driver.quit()
    print("✅ Scraping terminé !")