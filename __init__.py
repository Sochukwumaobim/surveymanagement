# -*- coding: utf-8 -*-
"""
/***************************************************************************
 SurveyManagement
                                 A QGIS plugin
 Digital archiving for Nigerian survey records
                             -------------------
        begin                : 2026-03-10
        copyright            : (C) 2026 by ASTROMAT GEO-SERVICES
        email                : ugwusochukwuma@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
 This script initializes the plugin, making it known to QGIS.
"""

# NOTE: Do NOT add lib/ to sys.path here at module load time.
# Doing so at __init__.py level causes QGIS to find the wrong
# 'surveymanagement' module when lib/ is first on sys.path,
# resulting in "module has no attribute 'classFactory'".
#
# lib/ is added to sys.path lazily inside dependency_manager.py
# and dxf_importer.py only when actually needed.


# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load SurveyManagement class from file SurveyManagement.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    from .SurveyManagement import SurveyManagement
    return SurveyManagement(iface)
