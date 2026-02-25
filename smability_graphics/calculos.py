import math

class CalculadoraRiesgoSmability:
    def __init__(self):
        self.K_CIGARRO = 22.0  
        self.K_O3_A_PM = 0.5   
        self.K_ENVEJECIMIENTO = 2.0  
        self.FACTOR_INTRAMUROS = 0.4 
        
        self.FACTORES_TRANSPORTE = {
            "auto_ac": 0.4, "suburbano": 0.5, "cablebus": 0.7,
            "metro": 0.8, "metrobus": 0.9, "auto_ventana": 1.0,
            "combi": 1.2, "caminar": 1.3, "bicicleta": 1.5, "home_office": 1.0
        }

    def calcular_usuario(self, vector_casa, perfil_usuario, vector_trabajo=None, es_home_office=False):
        if not vector_casa: return None

        if es_home_office or not vector_trabajo:
            vector_trabajo = vector_casa
            hora_salida, hora_llegada_casa, factor_transporte = 25, 25, 1.0
        else:
            # Valores por defecto si fallan los datos
            try:
                hora_salida = 7  
                duracion_traslado = float(perfil_usuario.get('tiempo_traslado_horas', 2)) 
                mitad_traslado = math.ceil(duracion_traslado / 2.0)
                hora_llegada_trabajo = hora_salida + mitad_traslado
                hora_salida_trabajo = 18 
                hora_llegada_casa = hora_salida_trabajo + mitad_traslado
                modo_transporte = perfil_usuario.get('transporte_default', 'auto_ventana')
                factor_transporte = self.FACTORES_TRANSPORTE.get(modo_transporte, 1.0)
            except:
                 hora_salida, hora_llegada_casa, factor_transporte = 25, 25, 1.0 # Fallback seguro

        suma_exposicion_acumulada = 0.0
        suma_ias_acumulada = 0.0 
        
        # Get safe con valores default para evitar crasheos por datos faltantes
        vector_casa_ias = vector_casa.get('ias', [0]*24)
        vector_trabajo_ias = vector_trabajo.get('ias', [0]*24)
        c_pm25 = vector_casa.get('pm25_12h', [0.0]*24)
        c_o3 = vector_casa.get('o3_1h', [0.0]*24)
        t_pm25 = vector_trabajo.get('pm25_12h', [0.0]*24)
        t_o3 = vector_trabajo.get('o3_1h', [0.0]*24)

        # Validación extra: asegurar que las listas tengan 24 elementos
        if len(c_pm25) < 24: c_pm25 = [0.0]*24
        if len(t_pm25) < 24: t_pm25 = [0.0]*24
        if len(vector_casa_ias) < 24: vector_casa_ias = [0]*24

        for hora in range(24):
            # Cálculo de exposición exterior combinada (PM2.5 equivalente)
            ext_casa = c_pm25[hora] + (c_o3[hora] * self.K_O3_A_PM)
            ext_trab = t_pm25[hora] + (t_o3[hora] * self.K_O3_A_PM)
            
            ias_ext_casa = vector_casa_ias[hora]
            ias_ext_trab = vector_trabajo_ias[hora]

            # Aplicación de factor intramuros (protección de edificios)
            int_casa = ext_casa * self.FACTOR_INTRAMUROS
            int_trab = ext_trab * self.FACTOR_INTRAMUROS
            
            ias_int_casa = ias_ext_casa * self.FACTOR_INTRAMUROS
            ias_int_trab = ias_ext_trab * self.FACTOR_INTRAMUROS

            if es_home_office:
                # Si es HO, siempre está protegido en casa
                nivel_hora, ias_hora = int_casa, ias_int_casa
            else:
                # Lógica de rutina de traslado
                if hora < hora_salida or hora >= hora_llegada_casa:
                    # Está en casa
                    nivel_hora, ias_hora = int_casa, ias_int_casa
                elif hora_salida <= hora < hora_llegada_trabajo:
                    # Traslado Ida (Expuesto al exterior * factor transporte)
                    nivel_hora = ((ext_casa + ext_trab) / 2) * factor_transporte 
                    ias_hora = ((ias_ext_casa + ias_ext_trab) / 2) * factor_transporte
                elif hora_llegada_trabajo <= hora < hora_salida_trabajo:
                    # Está en la oficina
                    nivel_hora, ias_hora = int_trab, ias_int_trab
                elif hora_salida_trabajo <= hora < hora_llegada_casa:
                    # Traslado Regreso
                    nivel_hora = ((ext_casa + ext_trab) / 2) * factor_transporte 
                    ias_hora = ((ias_ext_casa + ias_ext_trab) / 2) * factor_transporte

            suma_exposicion_acumulada += nivel_hora
            suma_ias_acumulada += ias_hora

        # Promedios finales de 24 horas
        promedio = suma_exposicion_acumulada / 24.0
        cigarros = promedio / self.K_CIGARRO
        promedio_ias = math.ceil(suma_ias_acumulada / 24.0) 
        
        # Categorización IAS
        cat_ias = "Buena" if promedio_ias <= 50 else "Regular" if promedio_ias <= 100 else "Mala" if promedio_ias <= 150 else "Muy Mala" if promedio_ias <= 200 else "Extremadamente Mala"
        
        return {
            "cigarros": round(cigarros, 1), 
            "dias_perdidos": round(cigarros * self.K_ENVEJECIMIENTO, 1),
            "promedio_riesgo": round(promedio, 1),
            "promedio_ias": promedio_ias,
            "calidad_ias_texto": cat_ias 
        }
