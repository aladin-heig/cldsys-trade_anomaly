import { Authenticator, translations } from "@aws-amplify/ui-react";
import "@aws-amplify/ui-react/styles.css";
import { Amplify } from "aws-amplify";
import { I18n } from "aws-amplify/utils";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./index.css";

Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId: import.meta.env.VITE_USER_POOL_ID,
      userPoolClientId: import.meta.env.VITE_USER_POOL_CLIENT_ID,
      loginWith: { email: true },
    },
  },
});
I18n.putVocabularies(translations);
I18n.setLanguage("fr");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* Accounts are created by an administrator (see DEPLOY.md), so sign-up is hidden. */}
    <Authenticator hideSignUp>
      {({ user, signOut }) => <App userEmail={user?.signInDetails?.loginId} onSignOut={signOut} />}
    </Authenticator>
  </StrictMode>,
);
