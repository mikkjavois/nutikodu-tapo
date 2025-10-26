# Nutikodu

Tapo nutipistikute reguleerimine elektri börsihinna järgi JavaScript kasutajaliidesega.

## Käivitamine

#### 0. Autentimine
Looge fail `env.py` ja lisage oma Tapo kasutajaandmed:  

```python
# env.py
CRED = ["tapo.user@example.com", "password"]
```
#### 1. Teekide installeerimine
```bash
pip install -r requirements.txt
```
#### 2. Käivitamine
```bash
python main.py
```