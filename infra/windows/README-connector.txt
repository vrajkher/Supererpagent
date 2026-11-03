RAJ Connector - quick guide
===========================

The RAJ Connector connects this PC's Tally to your CA firm's RAJ Finance Agent. It runs in the
background (also after a restart). It only reaches the one Tally company chosen during pairing.
Posting to Tally stays OFF until BOTH the firm and this PC allow it.

Before pairing
  1. Open TallyPrime with the client's company.
  2. F1 Help > Settings > Connectivity: "TallyPrime acts as" = Both, Port = 9000. Restart TallyPrime.

Pair (once)
  Start menu > RAJ Connector > "Pair this PC with the firm".
  Enter the firm server address and the pairing code the firm gave you (valid 24 hours, single use).
  Confirm the company when asked.

Everyday
  Start menu > RAJ Connector > "Connector status"   shows Tally, company and tunnel status
  "Pause connector" / "Resume connector"             stop or allow all new work from the firm
  "Allow posting on this PC" / "Block posting"       the local posting switch
  "Open connector log"                               C:\ProgramData\RAJ\connector.log

Keep TallyPrime open with the company loaded while the firm is working on your books.

Uninstall
  Settings > Apps > RAJ Connector > Uninstall. Then ask the firm to revoke this device.

------------------------------------------------------------------------------------------------
RAJ Connector - ટૂંકી માર્ગદર્શિકા
------------------------------------------------------------------------------------------------

આ RAJ Connector તમારા PC ના Tally ને તમારી CA firm ના RAJ Finance Agent સાથે જોડે છે.
તે background માં ચાલે છે, PC restart થાય પછી પણ.
Pairing વખતે પસંદ કરેલી એક જ Tally company સુધી તે પહોંચી શકે છે.
Firm અને આ PC બંને મંજૂરી આપે ત્યાં સુધી Tally માં posting બંધ રહે છે.

Pairing પહેલાં
  1. TallyPrime માં client ની company ખોલો.
  2. F1 Help > Settings > Connectivity માં "TallyPrime acts as" = Both અને Port = 9000 રાખો.
     પછી TallyPrime બંધ કરીને ફરી ખોલો.

Pairing (માત્ર એક વાર)
  Start menu > RAJ Connector > "Pair this PC with the firm" ખોલો.
  Firm નું server address અને pairing code લખો. Code 24 કલાક માટે અને એક જ વાર ચાલે છે.
  પૂછવામાં આવે ત્યારે company ની ખાતરી કરો.

રોજિંદું કામ
  "Connector status"                          Tally, company અને જોડાણની સ્થિતિ બતાવે છે
  "Pause connector" / "Resume connector"      firm નું નવું કામ રોકો અથવા ફરી ચાલુ કરો
  "Allow posting" / "Block posting"           આ PC પરની posting ની મંજૂરી (switch)
  "Open connector log"                        C:\ProgramData\RAJ\connector.log

Firm તમારા books પર કામ કરતી હોય ત્યારે TallyPrime માં company ખુલ્લી રાખો.

Uninstall
  Settings > Apps > RAJ Connector > Uninstall કરો.
  પછી firm ને આ device revoke કરવા કહો.
