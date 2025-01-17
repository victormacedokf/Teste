from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import mysql.connector
import pdfplumber
import re
import os
import json

# Configuração do Banco de Dados MySQL

DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "database": "srv6_bip",
    "user": "srv6_komforta",
    "password": "Komforta@123"
}


# Inicializar o app FastAPI
app = FastAPI()

# Configuração de CORS para permitir comunicação com o front-end
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def setup_database():
    """Configura as tabelas no banco de dados."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # Criar a tabela 'envios'
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS envios (
                numero_frete VARCHAR(50) PRIMARY KEY,
                produtos_envio INT,
                total_unidades INT,
                volumes_bipados INT DEFAULT 0,
                ultimo_tipo_bipado VARCHAR(20) DEFAULT NULL,
                status VARCHAR(255),
                progresso VARCHAR(255)
            )
        """)

        # Criar a tabela 'produtos'
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS produtos (
                id INT AUTO_INCREMENT PRIMARY KEY,
                numero_frete VARCHAR(50),
                codigo_ml VARCHAR(255),
                quantidade INT,
                quantidade_bipada INT DEFAULT 0,
                FOREIGN KEY (numero_frete) REFERENCES envios (numero_frete)
            )
        """)

        conn.commit()
        cursor.close()
        conn.close()
        print("Configuração do banco de dados concluída.")
    except mysql.connector.Error as e:
        print(f"Erro ao configurar o banco de dados: {e}")


def extract_data_from_pdf(pdf_path):
    """Extrai os dados do PDF usando a lógica existente."""
    data = {
        "envio": {
            "numero_frete": None,
            "produtos_envio": None,
            "total_unidades": None
        },
        "produtos": []
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = "\n".join(page.extract_text() for page in pdf.pages if page.extract_text())

        # Extração do número do frete
        frete_match = re.search(r"Frete\s*#(\d+)", full_text)
        data["envio"]["numero_frete"] = frete_match.group(1) if frete_match else "Não encontrado"

        # Extração de produtos do envio
        produtos_envio_match = re.search(r"Produtos do envio:\s*(\d+)", full_text)
        data["envio"]["produtos_envio"] = int(produtos_envio_match.group(1)) if produtos_envio_match else 0

        # Extração do total de unidades
        total_unidades_match = re.search(r"Total de unidades:\s*([\d.,]+)", full_text)
        if total_unidades_match:
            total_unidades = total_unidades_match.group(1).replace('.', '').replace(',', '')
            data["envio"]["total_unidades"] = int(total_unidades)
        else:
            data["envio"]["total_unidades"] = 0

        # Extração dos produtos
        produto_pattern = re.compile(
            r"Código ML:\s*([\w\d]+).*?Código universal:\s*([\d.,]+).*?SKU:.*?Kit",
            re.DOTALL
        )
        for match in produto_pattern.finditer(full_text):
            codigo_ml = match.group(1)
            quantidade = int(match.group(2).replace('.', '').replace(',', ''))
            data["produtos"].append({
                "numero_frete": data["envio"]["numero_frete"],
                "codigo_ml": codigo_ml,
                "quantidade": quantidade
            })

    except Exception as e:
        print(f"Erro ao processar PDF: {e}")
        raise Exception(f"Erro ao processar PDF: {e}")

    return data


def insert_data_into_database(envio_data, produtos_data):
    """Insere os dados no banco de dados."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # Inserir na tabela 'envios'
        cursor.execute("""
            INSERT INTO envios (numero_frete, produtos_envio, total_unidades, status, progresso)
            VALUES (%s, %s, %s, %s, %s)
        """, (envio_data["numero_frete"], envio_data["produtos_envio"], envio_data["total_unidades"], "Pendente", "0%"))

        # Inserir na tabela 'produtos'
        for produto in produtos_data:
            cursor.execute("""
                INSERT INTO produtos (numero_frete, codigo_ml, quantidade)
                VALUES (%s, %s, %s)
            """, (produto["numero_frete"], produto["codigo_ml"], produto["quantidade"]))

        # Inserir volumes na tabela 'volumes'
        for i in range(1, envio_data["total_unidades"] + 1):
            cursor.execute("""
                INSERT INTO volumes (numero_frete, numero_volume, bipado)
                VALUES (%s, %s, %s)
            """, (envio_data["numero_frete"], i, False))

        conn.commit()
        cursor.close()
        conn.close()
        print(f"Dados inseridos para o frete: {envio_data['numero_frete']}")
    except mysql.connector.Error as e:
        print(f"Erro ao inserir no banco: {e}")
        raise Exception(f"Erro ao inserir no banco: {e}")

@app.post("/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    """Processa o PDF enviado e insere os dados no banco."""
    try:
        # Salvar arquivo temporário
        temp_path = f"temp_{file.filename}"
        with open(temp_path, "wb") as f:
            f.write(await file.read())

        # Extrair dados do PDF
        data = extract_data_from_pdf(temp_path)

        # Inserir dados no banco
        insert_data_into_database(data["envio"], data["produtos"])

        # Remover o arquivo temporário
        os.remove(temp_path)

        return {"message": "PDF processado e enviado para o banco com sucesso."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro: {e}")


@app.get("/envios")
def listar_envios():
    """Lista os envios no banco de dados."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT * FROM envios")
        envios = cursor.fetchall()

        cursor.close()
        conn.close()
        return envios
    except mysql.connector.Error as e:
        print(f"Erro ao buscar envios: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao buscar envios: {e}")


@app.get("/envios/search/{numero_frete}")
def buscar_envio(numero_frete: str):
    """Busca um envio pelo número do frete."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT * FROM envios WHERE numero_frete = %s", (numero_frete,))
        envio = cursor.fetchone()

        if not envio:
            raise HTTPException(status_code=404, detail="Envio não encontrado.")

        cursor.close()
        conn.close()
        return envio
    except mysql.connector.Error as e:
        print(f"Erro ao buscar envio: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao buscar envio: {e}")


@app.delete("/envios/{numero_frete}")
def excluir_envio(numero_frete: str):
    """Exclui um envio e seus produtos do banco de dados."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # Excluir os volumes relacionados
        cursor.execute("DELETE FROM volumes WHERE numero_frete = %s", (numero_frete,))
        
        # Excluir os produtos relacionados
        cursor.execute("DELETE FROM produtos WHERE numero_frete = %s", (numero_frete,))
        
        # Excluir o envio
        cursor.execute("DELETE FROM envios WHERE numero_frete = %s", (numero_frete,))

        conn.commit()
        cursor.close()
        conn.close()

        return {"message": "Envio excluído com sucesso."}
    except mysql.connector.Error as e:
        print(f"Erro ao excluir envio: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao excluir envio: {e}")

@app.get("/bipagem/{numero_frete}")
def get_bipagem(numero_frete: str):
    """Retorna os dados do envio para bipagem."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT * FROM envios WHERE numero_frete = %s", (numero_frete,))
        envio = cursor.fetchone()

        cursor.execute("SELECT * FROM produtos WHERE numero_frete = %s", (numero_frete,))
        produtos = cursor.fetchall()

        cursor.close()
        conn.close()

        return {"envio": envio, "produtos": produtos}
    except mysql.connector.Error as e:
        print(f"Erro ao buscar dados para bipagem: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao buscar dados para bipagem: {e}")

# Modelo para o corpo da requisição
class BipagemRequest(BaseModel):
    codigo: str  # Código do volume ou produto
    sequenciaAtivada: bool  # Estado da sequência de bipagem

@app.post("/bipar/{numero_frete}")
def bipar_codigo(numero_frete: str, request: BipagemRequest):
    """
    Registra a bipagem de um produto ou volume com base na etiqueta recebida
    e no estado da sequência.
    """
    try:
        codigo = request.codigo
        sequencia_ativada = request.sequenciaAtivada
        print(f"Etiqueta recebida: {codigo}, Sequência ativada: {sequencia_ativada}")

        # Conexão com o banco de dados
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        # Identifica se a etiqueta é um volume ou um produto
        if codigo.startswith("{") and codigo.endswith("}"):
            # Caso seja um volume (etiqueta em JSON)
            etiqueta = json.loads(codigo)
            id_dados = etiqueta.get("id", "")
            if "/" not in id_dados:
                raise HTTPException(status_code=400, detail="Formato de etiqueta inválido.")

            numero_frete_extraido, numero_volume = id_dados.split("/")
            numero_volume = int(numero_volume)

            # Validação do frete
            if numero_frete != numero_frete_extraido:
                raise HTTPException(
                    status_code=400,
                    detail=f"Etiqueta pertence ao frete {numero_frete_extraido}, mas você está bipando o frete {numero_frete}."
                )

            # Verifica se o volume já foi bipado
            cursor.execute(
                """
                SELECT bipado FROM volumes
                WHERE numero_frete = %s AND numero_volume = %s
                """,
                (numero_frete, numero_volume)
            )
            volume = cursor.fetchone()

            if not volume:
                raise HTTPException(status_code=404, detail=f"Volume {numero_volume} não encontrado no frete {numero_frete}.")

            if volume["bipado"]:
                raise HTTPException(status_code=400, detail=f"Volume {numero_volume} já foi bipado.")

            # Se a sequência estiver ativada, verificar o último tipo bipado
            if sequencia_ativada:
                cursor.execute(
                    "SELECT ultimo_tipo_bipado FROM envios WHERE numero_frete = %s",
                    (numero_frete,)
                )
                envio = cursor.fetchone()

                if envio["ultimo_tipo_bipado"] == "volume":
                    raise HTTPException(status_code=400, detail="Você deve bipar um produto antes de bipar outro volume.")

            # Atualiza o volume como bipado
            cursor.execute(
                """
                UPDATE volumes
                SET bipado = TRUE
                WHERE numero_frete = %s AND numero_volume = %s
                """,
                (numero_frete, numero_volume)
            )

            # Atualiza o último tipo bipado no envio
            cursor.execute(
                """
                UPDATE envios
                SET volumes_bipados = volumes_bipados + 1, ultimo_tipo_bipado = 'volume'
                WHERE numero_frete = %s
                """,
                (numero_frete,)
            )

            conn.commit()
            cursor.close()
            conn.close()

            return {"message": f"Volume {numero_volume} bipado com sucesso."}

        else:
            # Caso seja um produto (etiqueta simples)
            cursor.execute(
                """
                SELECT quantidade, quantidade_bipada FROM produtos
                WHERE numero_frete = %s AND codigo_ml = %s
                """,
                (numero_frete, codigo)
            )
            produto = cursor.fetchone()

            if not produto:
                raise HTTPException(status_code=404, detail=f"Produto {codigo} não encontrado no frete {numero_frete}.")

            if produto["quantidade_bipada"] >= produto["quantidade"]:
                raise HTTPException(status_code=400, detail=f"Produto {codigo} já foi bipado completamente.")

            # Se a sequência estiver ativada, verificar o último tipo bipado
            if sequencia_ativada:
                cursor.execute(
                    "SELECT ultimo_tipo_bipado FROM envios WHERE numero_frete = %s",
                    (numero_frete,)
                )
                envio = cursor.fetchone()

                if envio["ultimo_tipo_bipado"] == "produto":
                    raise HTTPException(status_code=400, detail="Você deve bipar um volume antes de bipar outro produto.")

            # Atualiza a quantidade bipada do produto
            cursor.execute(
                """
                UPDATE produtos
                SET quantidade_bipada = quantidade_bipada + 1
                WHERE numero_frete = %s AND codigo_ml = %s
                """,
                (numero_frete, codigo)
            )

            # Atualiza o último tipo bipado no envio
            cursor.execute(
                """
                UPDATE envios
                SET ultimo_tipo_bipado = 'produto'
                WHERE numero_frete = %s
                """,
                (numero_frete,)
            )

            conn.commit()
            cursor.close()
            conn.close()

            return {"message": f"Produto {codigo} bipado com sucesso."}

    except HTTPException as e:
        raise e
    except Exception as e:
        print("Erro ao processar bipagem:", e)
        raise HTTPException(status_code=500, detail=f"Erro ao processar bipagem: {e}")

@app.post("/salvar/{numero_frete}")
def salvar_bipagem(numero_frete: str):
    """
    Atualiza o status do envio com base no progresso atual da bipagem.
    """
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        # Obter o total de produtos, produtos bipados, volumes e volumes bipados
        cursor.execute("""
            SELECT 
                (SELECT COUNT(*) FROM produtos WHERE numero_frete = %s) AS total_produtos,
                (SELECT SUM(quantidade_bipada) FROM produtos WHERE numero_frete = %s) AS total_produtos_bipados,
                (SELECT total_unidades FROM envios WHERE numero_frete = %s) AS total_volumes,
                (SELECT volumes_bipados FROM envios WHERE numero_frete = %s) AS volumes_bipados
        """, (numero_frete, numero_frete, numero_frete, numero_frete))
        resultado = cursor.fetchone()

        total_produtos = resultado['total_produtos'] or 0
        total_produtos_bipados = resultado['total_produtos_bipados'] or 0
        total_volumes = resultado['total_volumes'] or 0
        volumes_bipados = resultado['volumes_bipados'] or 0

        # Determinar o novo status
        if total_produtos_bipados == 0 and volumes_bipados == 0:
            novo_status = "Pendente"
        elif total_produtos_bipados < total_produtos or volumes_bipados < total_volumes:
            novo_status = "Em andamento"
        else:
            novo_status = "Aguardando aprovação"

        # Atualizar o status no banco de dados
        cursor.execute("""
            UPDATE envios
            SET status = %s
            WHERE numero_frete = %s
        """, (novo_status, numero_frete))

        conn.commit()
        cursor.close()
        conn.close()

        return {"message": f"Status atualizado para '{novo_status}' com sucesso."}
    except mysql.connector.Error as e:
        print(f"Erro ao salvar progresso: {e}")
        raise HTTPException(status_code=500, detail="Erro ao salvar progresso.")

def atualizar_progresso(numero_frete):
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # Atualizar progresso de volumes
    cursor.execute("""
        SELECT COUNT(*) as total_volumes, 
               SUM(CASE WHEN bipado = TRUE THEN 1 ELSE 0 END) as volumes_bipados 
        FROM volumes 
        WHERE numero_frete = %s
    """, (numero_frete,))
    volumes = cursor.fetchone()
    total_volumes = volumes[0]
    volumes_bipados = volumes[1]

    progresso_volumes = (volumes_bipados / total_volumes) * 100 if total_volumes > 0 else 0
    cursor.execute("UPDATE envios SET progresso = %s WHERE numero_frete = %s", 
                   (f"{progresso_volumes:.2f}%", numero_frete))

    conn.commit()
    cursor.close()
    conn.close()

def extrair_dados_volume(json_data: str):
    """
    Extrai informações de volume a partir do JSON.
    Exemplo de JSON:
    {
        "id": "35898086/1",
        "operation": "fbm",
        "type": "inbound",
        "source": "seller"
    }
    """
    try:
        # Converter o JSON recebido em um dicionário Python
        data = json.loads(json_data)

        # Extrair o `id` e dividir em `numero_frete` e `numero_volume`
        id_data = data.get("id", "")
        if not id_data or "/" not in id_data:
            raise ValueError("Formato de 'id' inválido ou não encontrado no JSON.")

        numero_frete, numero_volume = id_data.split("/")
        numero_volume = int(numero_volume)

        return numero_frete, numero_volume
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        print(f"Erro ao processar o JSON da etiqueta: {e}")
        return None, None

@app.get("/envios/{numero_frete}/volumes")
def listar_volumes(numero_frete: str):
    """
    Lista os volumes de um envio pelo número do frete.
    """
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT numero_volume, bipado
            FROM volumes
            WHERE numero_frete = %s
            """,
            (numero_frete,),
        )
        volumes = cursor.fetchall()

        cursor.close()
        conn.close()

        return {"volumes": volumes}
    except mysql.connector.Error as e:
        print(f"Erro ao buscar volumes: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao buscar volumes: {e}")

# Configuração inicial do banco
setup_database()
