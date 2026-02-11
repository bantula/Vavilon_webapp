import os
import json
import azure.cognitiveservices.speech as speechsdk
from babellore.logging.logging_config import get_audio_logger

# Initialize logger
logger = get_audio_logger()


def make_output_dir(input_path):
    """
    Generates the output file path based on the input file path based on the asumion that input files are in /Test-Input and output files are in /Test-Output.
    """
    input_dir = os.path.dirname(input_path)
    input_file_name = os.path.basename(input_path)
    output_dir = input_dir.replace("Test-Input", "Test-Output")
    output_path = os.path.join(output_dir, input_file_name.replace(".wav", "-Translated.wav"))

    return output_path


def pick_language_interface():
    """
    Interface for picking languages from a set list.
    """
    logger.info("Language selection interface started")
    print("Select the input language:")
    print("1. English")
    print("2. Serbian")
    print("3. French")
    print("4. German")
    print("5. Russian")
    print("6. Turkish")

    try:
        choice = int(input("Enter the number of the language: "))
        if 1 <= choice <= 6:
            languages = ["en-US", "sr-RS", "fr-FR", "de-DE", "ru-RU", "tr-TR"]
            selected_language = languages[choice - 1]
            logger.info("Language selected: %s", selected_language)
            print(f"You have selected: {selected_language}\n\n")
        else:
            logger.warning("Invalid language selection: %d", choice)
            print("Invalid selection. Please run the program again and choose a valid number.")
    except ValueError:
        logger.error("Invalid input in language selection - non-numeric input provided")
        print("Invalid input. Please enter a number corresponding to the language.")

    return selected_language


def pick_file_interface(dir):
    """
    Interface for picking a file from a given directory.
    
    Parameters:
        dir(str): Directory from witch to pick files from.
    """
    files = [f for f in os.listdir(dir) if os.path.isfile(os.path.join(dir, f))]
    logger.info("File selection interface started for directory: %s", dir)
    print("Files in the directory:")
    for idx, file in enumerate(files, start=1):
        print(f"{idx}. {file}")

    choice = int(input("Enter the number of the file you want to use: "))
    if 1 <= choice <= len(files):
        selected_file = files[choice - 1]
        logger.info("File selected: %s", selected_file)
        print(f"You have selected: {selected_file}\n")
        audio_file_path = os.path.join(dir, selected_file)
    else:
        logger.warning("Invalid file selection: %d", choice)
        print("Invalid selection. Please run the program again and choose a valid number.")

    output_path = make_output_dir(audio_file_path)
    logger.debug("Input path: %s", audio_file_path)
    logger.debug("Output path: %s", output_path)
    print(f"Input path: {audio_file_path}")
    print(f"Output path: {output_path}\n\n")

    return audio_file_path, output_path


def pick_voice_interface(language_code, speech_key, speech_region):
    """
    Interface for picking a voice from the list of all available voices for a given language.
    
    Parameters:
        language_code(str): (eg. 'en-US') The language that hte voice is chosen for.   
        speech_key, speech_region (str, str): Azure credentials loaded from environment variables.
    """

    # Create a SpeechConfig for synthesis and set the synthesis language filter.
    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=speech_region)
    # Setting the speech_synthesis_language helps limit the voices returned.
    speech_config.speech_synthesis_language = language_code

    # Create a synthesizer without audio output to fetch voice list.
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
    
    # Retrieve available voices
    voices_result = synthesizer.get_voices_async().get()
    all_voices = voices_result.voices

    # Filter voices: only those whose locale starts with the provided language code.
    filtered_voices = [v for v in all_voices if v.locale.lower().startswith(language_code.lower())]

    if not filtered_voices:
        logger.warning("No voices available for language code: %s", language_code)
        print(f"No voices available for language code '{language_code}'.")
        return None

    logger.info("Voice selection interface started for language: %s", language_code)
    print(f"Available voices for language '{language_code}':")
    for idx, voice in enumerate(filtered_voices):
        # Extract the short voice name by removing the locale and hyphen.
        # Example: if voice.name is "en-US-AmberNeural" and voice.locale is "en-US",
        # then short_name becomes "AmberNeural".
        prefix = voice.locale + "-"
        short_name = voice.name[len(prefix):] if voice.name.startswith(prefix) else voice.name
        print(f"[{idx}] {voice.locale}, {short_name}")

    # Prompt user until a valid number is entered.
    while True:
        selection = int(input("Enter the number corresponding to your chosen voice: "))
        if 0 <= selection < len(filtered_voices):
            chosen_voice = filtered_voices[selection].name
            logger.info("Voice selected: %s", chosen_voice)
            print(f"You selected: {filtered_voices[selection].locale}, {filtered_voices[selection].name[len(filtered_voices[selection].locale)+1:]}")
            return chosen_voice
        else:
            logger.warning("Invalid voice selection: %d", selection)
            print(f"Invalid selection. Please enter a number between 0 and {len(filtered_voices)-1}.")
    


def add_or_update_clients(clients_file_path, clients_data):
    """
    Add new clients or update existing clients in the clients.json file.
    Handles multiple clients and writes to file only once.
    
    Parameters:
        clients_file_path (str): Path to the clients.json file
        clients_data (list): List of dictionaries containing client information with keys:
                            'mac', 'name', 'ip', 'language'
    
    Returns:
        bool: True if all operations were successful, False otherwise
        list: List of status messages for each client operation
    """
    # Validate input
    if not isinstance(clients_data, list):
        return False, ["Input must be a list of client dictionaries"]
    
    if not clients_data:
        return False, ["No client data provided"]
    
    required_fields = ['mac', 'name', 'ip', 'language']
    status_messages = []
    
    # Validate all client data first using the dedicated validation function
    for i, client_data in enumerate(clients_data):
        is_valid, error_message = validate_client_data(client_data)
        if not is_valid:
            return False, [f"Client {i+1}: {error_message}"]
    
    try:
        # Load existing clients
        if os.path.exists(clients_file_path):
            with open(clients_file_path, 'r', encoding='utf-8') as f:
                clients = json.load(f)
        else:
            clients = []
        
        # Get all MAC addresses from new client data for batch lookup
        mac_addresses_to_check = [client_data['mac'].upper() for client_data in clients_data]
        existing_clients_dict, _ = find_clients_by_mac(clients_file_path, mac_addresses_to_check)
        
        # Process each client
        for client_data in clients_data:
            mac_address = client_data['mac'].upper()
            
            # Check if client already exists using the batch lookup result
            existing_client = existing_clients_dict.get(mac_address)
            existing_client_index = None
            
            if existing_client:
                # Find the index of the existing client
                for i, client in enumerate(clients):
                    if client.get('mac', '').upper() == mac_address:
                        existing_client_index = i
                        break
            
            # Prepare client data with consistent MAC format
            new_client_data = {
                'mac': mac_address,
                'name': client_data['name'],
                'ip': client_data['ip'],
                'language': client_data['language']
            }
            
            # Update existing client or add new one
            if existing_client_index is not None:
                old_client = clients[existing_client_index]
                clients[existing_client_index] = new_client_data
                status_messages.append(f"Updated client '{old_client.get('name', 'Unknown')}' (MAC: {mac_address})")
            else:
                clients.append(new_client_data)
                status_messages.append(f"Added new client '{client_data['name']}' (MAC: {mac_address})")
        
        # Create directory if it doesn't exist
        dir_path = os.path.dirname(clients_file_path)
        if dir_path:  # Only create directory if there's a directory path
            os.makedirs(dir_path, exist_ok=True)
        
        # Save updated clients list (only once)
        with open(clients_file_path, 'w', encoding='utf-8') as f:
            json.dump(clients, f, indent=4, ensure_ascii=False)
        
        return True, status_messages
        
    except json.JSONDecodeError as e:
        return False, [f"Error reading JSON file: {e}"]
    except IOError as e:
        return False, [f"Error accessing file: {e}"]
    except Exception as e:
        return False, [f"Unexpected error: {e}"]


def add_or_update_client(clients_file_path, client_data):
    """
    Add a new client or update an existing client in the clients.json file.
    This is a wrapper function for backwards compatibility.
    
    Parameters:
        clients_file_path (str): Path to the clients.json file
        client_data (dict): Dictionary containing client information with keys:
                           'mac', 'name', 'ip', 'language'
    
    Returns:
        bool: True if operation was successful, False otherwise
        str: Status message describing what happened
    """
    success, messages = add_or_update_clients(clients_file_path, [client_data])
    return success, messages[0] if messages else "No operation performed"


def remove_clients(clients_file_path, mac_addresses):
    """
    Remove multiple clients from the clients.json file by MAC addresses.
    Handles multiple clients and writes to file only once.
    
    Parameters:
        clients_file_path (str): Path to the clients.json file
        mac_addresses (list): List of MAC addresses of clients to remove
    
    Returns:
        bool: True if at least one client was removed, False otherwise
        list: List of status messages for each removal operation
    """
    # Validate input
    if not isinstance(mac_addresses, list):
        return False, ["Input must be a list of MAC addresses"]
    
    if not mac_addresses:
        return False, ["No MAC addresses provided"]
    
    # Normalize MAC addresses
    mac_addresses_upper = [mac.upper() for mac in mac_addresses]
    
    try:
        # Load existing clients
        if not os.path.exists(clients_file_path):
            return False, ["Clients file does not exist"]
        
        with open(clients_file_path, 'r', encoding='utf-8') as f:
            clients = json.load(f)
        
        # Use find_clients_by_mac to get existing clients info
        existing_clients_dict, find_messages = find_clients_by_mac(clients_file_path, mac_addresses_upper)
        
        # Find and collect clients to remove
        removed_clients = {}
        clients_updated = []
        status_messages = []
        
        for client in clients:
            client_mac = client.get('mac', '').upper()
            if client_mac in mac_addresses_upper and existing_clients_dict.get(client_mac):
                removed_clients[client_mac] = client
            else:
                clients_updated.append(client)
        
        # Generate status messages for each requested MAC
        for mac_address in mac_addresses_upper:
            if mac_address in removed_clients:
                client_name = removed_clients[mac_address].get('name', 'Unknown')
                status_messages.append(f"Removed client '{client_name}' (MAC: {mac_address})")
            else:
                status_messages.append(f"Client with MAC address {mac_address} not found")
        
        # Check if any clients were actually removed
        if not removed_clients:
            return False, status_messages
        
        # Create directory if it doesn't exist
        dir_path = os.path.dirname(clients_file_path)
        if dir_path:  # Only create directory if there's a directory path
            os.makedirs(dir_path, exist_ok=True)
        
        # Save updated clients list (only once)
        with open(clients_file_path, 'w', encoding='utf-8') as f:
            json.dump(clients_updated, f, indent=4, ensure_ascii=False)
        
        return True, status_messages
        
    except json.JSONDecodeError as e:
        return False, [f"Error reading JSON file: {e}"]
    except IOError as e:
        return False, [f"Error accessing file: {e}"]
    except Exception as e:
        return False, [f"Unexpected error: {e}"]


def remove_client(clients_file_path, mac_address):
    """
    Remove a client from the clients.json file by MAC address.
    This is a wrapper function for backwards compatibility.
    
    Parameters:
        clients_file_path (str): Path to the clients.json file
        mac_address (str): MAC address of the client to remove
    
    Returns:
        bool: True if client was removed, False otherwise
        str: Status message describing what happened
    """
    success, messages = remove_clients(clients_file_path, [mac_address])
    return success, messages[0] if messages else "No operation performed"


def get_all_clients(clients_file_path):
    """
    Get all clients from the clients.json file.
    
    Parameters:
        clients_file_path (str): Path to the clients.json file
    
    Returns:
        list: List of client dictionaries, empty list if file doesn't exist or error occurs
        str: Status message (empty string if successful)
    """
    try:
        if not os.path.exists(clients_file_path):
            return [], "Clients file does not exist"
        
        with open(clients_file_path, 'r', encoding='utf-8') as f:
            clients = json.load(f)
        
        return clients, ""
        
    except json.JSONDecodeError as e:
        return [], f"Error reading JSON file: {e}"
    except IOError as e:
        return [], f"Error accessing file: {e}"
    except Exception as e:
        return [], f"Unexpected error: {e}"


def find_client_by_mac(clients_file_path, mac_address):
    """
    Find a client by MAC address in the clients.json file.
    This is a wrapper function that uses find_clients_by_mac for consistency.
    
    Parameters:
        clients_file_path (str): Path to the clients.json file
        mac_address (str): MAC address to search for
    
    Returns:
        dict or None: Client data if found, None otherwise
        str: Status message
    """
    clients_dict, messages = find_clients_by_mac(clients_file_path, [mac_address])
    mac_upper = mac_address.upper()
    return clients_dict.get(mac_upper), messages[0] if messages else "No operation performed"


def find_clients_by_mac(clients_file_path, mac_addresses):
    """
    Find multiple clients by MAC addresses in the clients.json file.
    
    Parameters:
        clients_file_path (str): Path to the clients.json file
        mac_addresses (list): List of MAC addresses to search for
    
    Returns:
        dict: Dictionary mapping MAC addresses to client data (None if not found)
        list: List of status messages for each MAC address
    """
    if not isinstance(mac_addresses, list):
        return {}, ["Input must be a list of MAC addresses"]
    
    mac_addresses_upper = [mac.upper() for mac in mac_addresses]
    
    clients, error_message = get_all_clients(clients_file_path)
    if error_message:
        return {}, [error_message]
    
    # Create lookup dictionary for existing clients
    client_lookup = {}
    for client in clients:
        client_lookup[client.get('mac', '').upper()] = client
    
    # Find each requested MAC address
    found_clients = {}
    status_messages = []
    
    for mac_address in mac_addresses_upper:
        if mac_address in client_lookup:
            client = client_lookup[mac_address]
            found_clients[mac_address] = client
            status_messages.append(f"Found client '{client.get('name', 'Unknown')}' (MAC: {mac_address})")
        else:
            found_clients[mac_address] = None
            status_messages.append(f"Client with MAC address {mac_address} not found")
    
    return found_clients, status_messages


def validate_client_data(client_data):
    """
    Validate a single client data dictionary.
    
    Parameters:
        client_data (dict): Client data to validate
    
    Returns:
        bool: True if valid, False otherwise
        str: Error message if invalid, empty string if valid
    """
    required_fields = ['mac', 'name', 'ip', 'language']
    
    if not isinstance(client_data, dict):
        return False, "Client data must be a dictionary"
    
    if not all(field in client_data for field in required_fields):
        return False, f"Missing required fields. Required: {required_fields}"
    
    # Validate MAC address format
    mac_address = client_data['mac'].upper()
    if len(mac_address) != 17 or mac_address.count(':') != 5:
        return False, "Invalid MAC address format. Expected format: XX:XX:XX:XX:XX:XX"
    
    # Basic validation for other fields
    if not client_data['name'].strip():
        return False, "Client name cannot be empty"
    
    if not client_data['ip'].strip():
        return False, "IP address cannot be empty"
    
    if not client_data['language'].strip():
        return False, "Language cannot be empty"
    
    return True, ""

