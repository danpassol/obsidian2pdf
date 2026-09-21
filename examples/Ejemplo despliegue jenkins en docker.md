---
status: finished
title: Despliegue de Jenkins en Docker
subtitle: Ejemplo de guía paso a paso con callouts
author: Equipo de Documentación
author_email: docs@example.com
date: 2026-01-15
version: "1.2"
---

> [!abstract] Resumen
> Esta guía explica cómo levantar un servidor **Jenkins** dentro de un contenedor Docker, persistir sus datos, desbloquearlo y crear una primera *pipeline*. Sirve también de ejemplo de los **callouts** de Obsidian: cada tipo se dibuja con su propio color e icono.

# Requisitos previos

| Requisito | Versión mínima | Comprobación |
|---|---|---|
| Docker Engine | 24.0 | `docker --version` |
| Docker Compose | 2.20 | `docker compose version` |
| Memoria libre | 2 GB | `free -h` |
| Puertos libres | 8080 y 50000 | `ss -tlnp` |

Además necesitas permisos para ejecutar comandos de Docker (pertenecer al grupo `docker`).

# Arrancar Jenkins

> [!note] Imagen recomendada
> Usa siempre la imagen oficial `jenkins/jenkins` con una etiqueta **LTS** fija en lugar de `latest`, para que las actualizaciones sean una decisión tuya.

Crea un volumen para guardar los datos y arranca el contenedor:

```bash
docker volume create jenkins_home

docker run -d \
  --name jenkins \
  --restart unless-stopped \
  -p 8080:8080 \
  -p 50000:50000 \
  -v jenkins_home:/var/jenkins_home \
  jenkins/jenkins:lts-jdk17
```

> [!tip] Un volumen, no un directorio
> Un volumen con nombre (`jenkins_home`) evita problemas de permisos que sí aparecen al montar un directorio del anfitrión.

Comprueba que está en marcha:

```bash
docker ps --filter name=jenkins
docker logs -f jenkins
```

> [!success] Todo correcto
> Cuando en el log aparezca `Jenkins is fully up and running` el servidor ya responde en <http://localhost:8080>.

## Con Docker Compose

Para no repetir opciones, es más cómodo describir el servicio en un fichero:

```yaml
# docker-compose.yml
services:
  jenkins:
    image: jenkins/jenkins:lts-jdk17
    container_name: jenkins
    restart: unless-stopped
    ports:
      - "8080:8080"
      - "50000:50000"
    volumes:
      - jenkins_home:/var/jenkins_home
    environment:
      - JAVA_OPTS=-Xmx1g -Djenkins.install.runSetupWizard=true

volumes:
  jenkins_home:
```

```bash
docker compose up -d
```

> [!info] Puerto 50000
> El puerto `50000` lo usan los agentes que se conectan al servidor. Si solo vas a ejecutar trabajos en el propio Jenkins, puedes no publicarlo.

# Desbloquear Jenkins

La primera vez, Jenkins pide una contraseña de administrador que genera en el arranque:

```bash
docker exec jenkins cat /var/jenkins_home/secrets/initialAdminPassword
```

Copia el valor en el asistente, instala los *plugins* sugeridos y crea tu usuario administrador.

> [!todo] Después del asistente
> - Cambiar la URL de Jenkins en **Administrar Jenkins → Sistema**
> - Crear un usuario por persona, sin compartir el de administrador
> - Configurar una copia de seguridad del volumen `jenkins_home`

# Primera pipeline

Crea un fichero `Jenkinsfile` en la raíz de tu repositorio:

```groovy
pipeline {
    agent any

    stages {
        stage('Compilar') {
            steps {
                sh 'make build'
            }
        }
        stage('Tests') {
            steps {
                sh 'make test'
            }
        }
        stage('Empaquetar') {
            when { branch 'main' }
            steps {
                sh 'docker build -t acme/app:${BUILD_NUMBER} .'
            }
        }
    }

    post {
        failure {
            echo 'La pipeline ha fallado'
        }
    }
}
```

> [!example] Ejecutar la pipeline
> 1. En Jenkins, elige **Nueva tarea → Pipeline**.
> 2. En *Definition* selecciona **Pipeline script from SCM**.
> 3. Indica la URL de tu repositorio y la rama `main`.
> 4. Pulsa **Construir ahora** y abre el resultado en *Console Output*.

# Seguridad

> [!warning] No expongas Jenkins a Internet sin más
> Publica el puerto `8080` solo detrás de un proxy inverso con HTTPS y autenticación. Un Jenkins abierto permite ejecutar código en tu servidor.

Para que la pipeline construya imágenes Docker, muchas guías montan el socket del anfitrión dentro del contenedor:

```bash
-v /var/run/docker.sock:/var/run/docker.sock
```

> [!danger] El socket de Docker equivale a acceso root
> Quien controle ese socket puede arrancar cualquier contenedor con privilegios sobre el anfitrión. Úsalo solo en entornos de confianza y valora alternativas como agentes dedicados o *builders* sin *daemon*.

# Solución de problemas

> [!question] ¿Jenkins no arranca y el log habla de permisos?
> Suele ocurrir al montar un directorio del anfitrión en `/var/jenkins_home` que no pertenece al usuario `1000`. Cámbiale el propietario con `sudo chown -R 1000:1000 <directorio>` o usa un volumen con nombre.

> [!bug] «Address already in use» al arrancar
> Otro proceso ya usa el puerto `8080`. Localízalo con `ss -tlnp | grep 8080` o publica Jenkins en otro puerto: `-p 9090:8080`.

> [!failure] La pipeline falla con «docker: command not found»
> El contenedor de Jenkins no incluye el cliente de Docker. Instálalo en una imagen derivada o ejecuta esa etapa en un agente que sí lo tenga.

> [!quote] Una regla práctica
> Si no puedes reconstruir tu servidor de integración continua en menos de una hora a partir del repositorio y de una copia del volumen, todavía no lo tienes bien documentado.

# Siguientes pasos

- Conecta la pipeline con tu repositorio mediante *webhooks*.
- Añade agentes efímeros en contenedores para aislar las compilaciones.
- Consulta la [[Ejemplo documentación técnica|documentación técnica]] de un servicio real para ver cómo encaja en una arquitectura.
