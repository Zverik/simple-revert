#Common things for oauth with OSM
#Written for API 0.6's OAuth
import os
import json
import requests_oauthlib as oauth

CONFIG_NAME = os.path.join(os.path.dirname(__file__), 'authConfig.json')
API_ENDPOINT = 'https://www.openstreetmap.org/api/0.6/'
OAUTH_ENDPOINT = 'https://www.openstreetmap.org/oauth/'

#Save the OAuth details to file
def writeAuthConfig(client_key: str, client_secret: str, token: str, token_secret: str) -> bool:
    config = {
        "client_key": client_key,
        "client_secret": client_secret,
        "token": token,
        "token_secret": token_secret
    }

    #Save to file
    with open(CONFIG_NAME, 'w') as configFile:
        json.dump(config, configFile)

#Load the OAuth details from file
def loadAuthConfig() -> dict:
    try:
        with open(CONFIG_NAME, 'r') as configFile:
            return json.load(configFile)
    except:
        return None

#Let the user authorize himself, save the token and return the info
def authorizeUser():
    #Collect details
    print("You seem to not be authentificated yet.\nPlease register an OAuth1 application on OSM (make sure to enter any redirect URL) and enter the details.")
    client_key = input("Client key: ")
    client_secret = input("Client secret: ")

    #Fetch request token
    authSession = oauth.OAuth1Session(client_key=client_key, client_secret=client_secret)
    request_token = authSession.fetch_request_token(OAUTH_ENDPOINT + "request_token")

    #Authorize user and read redirect URL
    userAuthUrl = authSession.authorization_url(OAUTH_ENDPOINT + "authorize")
    print("Please authorize yourself at {0}.".format(userAuthUrl))
    userRedirectUrl = input("Please enter the URL you have been redirected to: ")

    #Parse temporary verifier
    authorizationResponse = authSession.parse_authorization_response(userRedirectUrl)
    verifier = authorizationResponse["oauth_token"]

    #Fetch final access token
    authSession = oauth.OAuth1Session(
                        client_key=client_key,
                        client_secret=client_secret,
                        resource_owner_key=request_token["oauth_token"],
                        resource_owner_secret=request_token["oauth_token_secret"],
                        verifier=verifier)
    access_token = authSession.fetch_access_token(OAUTH_ENDPOINT + "access_token")

    #Check OAuth
    authSession = oauth.OAuth1Session(
                    client_key=client_key,
                    client_secret=client_secret,
                    resource_owner_key=access_token["oauth_token"],
                    resource_owner_secret=access_token["oauth_token_secret"]
    )

    authCheck = authSession.get(API_ENDPOINT + "user/details")
    if(not authCheck.ok):
        print(authCheck.url)
        print(authCheck.content)
        print("Something seems to have gone wrong authentificating you.")
        return

    #Save the details to the authConfig
    writeAuthConfig(client_key, client_secret, access_token["oauth_token"], access_token["oauth_token_secret"])
    print("Authorization complete. Thank you!")

#Get the user's OAuth, call authorizeUser() if not authed yet
def getUserAuthentification() -> oauth.OAuth1:
    authConfig = loadAuthConfig()
    while authConfig == None:
        authorizeUser()
        authConfig = loadAuthConfig()

    return oauth.OAuth1(
        client_key=authConfig["client_key"],
        client_secret=authConfig["client_secret"],
        resource_owner_key=authConfig["token"],
        resource_owner_secret=authConfig["token_secret"],
    )