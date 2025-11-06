# ... (kod pre handle_commands)

@bot.message_handler(commands=['start', 'stop', 'pokreni'])
def handle_commands(message):
    
    session = Session()
    try:
        if not is_game_active():
            send_msg(message, TIME_LIMIT_MESSAGE)
            return

        is_db_active = session is not None and Session is not None

        if not is_db_active: 
            send_msg(message, "⚠️ UPOZORENJE: Trajno stanje (DB) nije dostupno. Igrate u test modu bez pamćenja napretka.")
            if message.text.lower() in ['/start', 'start']:
                start_message_raw = GAME_STAGES["START_PROVERA"]["text"][0]
                
                # V10.59: Uklonjeno generisanje Glitch teksta.
                messages_to_send = [start_message_raw] 
                
                send_msg(message, messages_to_send)
            return

        chat_id = str(message.chat.id)

        if message.text.lower() in ['/start', 'start']:
            current_time = int(time.time())
            player = session.query(PlayerState).filter_by(chat_id=chat_id).first()
            if player:
                # Brišemo prethodno stanje da bi igrač mogao ponovo da krene
                session.delete(player)
                session.commit()
                # Ponovo ga kreiramo sa početnim stanjem
                user = message.from_user
                display_name = user.username or f"{user.first_name} {user.last_name or ''}".strip()
                player = PlayerState(
                    chat_id=chat_id, current_riddle="START_PROVERA", solved_count=0, 
                    score=0, 
                    conversation_history='[]',
                    is_disqualified=False, username=display_name, general_conversation_count=0,
                    start_time=current_time
                )
                session.add(player)
            else:
                user = message.from_user
                display_name = user.username or f"{user.first_name} {user.last_name or ''}".strip()
                player = PlayerState(
                    chat_id=chat_id, current_riddle="START_PROVERA", solved_count=0, 
                    score=0, 
                    conversation_history='[]',
                    is_disqualified=False, username=display_name, general_conversation_count=0,
                    start_time=current_time
                )
                session.add(player)

            session.commit()
            
            # V10.59 FIX: Slanje samo Provere Signala
            start_message_raw = GAME_STAGES["START_PROVERA"]["text"][0]
            
            # V10.59: Slanje samo jedne poruke - DA LI VIDIŠ MOJU PORUKU?
            messages_to_send = [start_message_raw]
            
            send_msg(message, messages_to_send)


        elif message.text.lower() in ['/stop', 'stop']:
# ... (ostatak handle_commands i ceo handle_general_message ostaju nepromenjeni od V10.58)
